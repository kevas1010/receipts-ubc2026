"""Discord bot: auto-lock picks in #moneyline channels, catch edits and deletes, /record, /leaderboard,
/import_x, and Verified Capper role sync. Run with:  python -m receipts.bot"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone

import discord
import httpx
from discord import app_commands
from discord.ext import tasks

from . import chain, db, odds, service, verify
from .config import settings
from .gemini import GeminiClient
from .snowflake import discord_time, parse_x_url, x_time

log = logging.getLogger("receipts.bot")
GEMINI_GREEN = 0x00DCFA
gemini = GeminiClient()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# message_id -> list of (ticker, title) awaiting "which game?" answer
pending_choices: dict[int, dict] = {}


def capper_for(user: discord.abc.User) -> dict:
    return service.ensure_capper(f"discord:{user.id}", user.display_name,
                                 str(user.display_avatar.url) if user.display_avatar else None)


def fmt_pct(p: float | None) -> str:
    return f"{p * 100:.0f}%" if p else "n/a"


def lock_embed(res: service.LockResult, capper: dict, rec: verify.Record | None) -> discord.Embed:
    p = res.pick
    start = int(datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).timestamp())
    e = discord.Embed(title=f"Locked: {p['side_label']} ML · {p['units']:g}u", color=GEMINI_GREEN,
                      description=f"**{p['event_title']}**\nStarts <t:{start}:f> (<t:{start}:R>)")
    e.add_field(name="Gemini price at lock", value=f"{fmt_pct(p['lock_price'])} implied", inline=True)
    tx = chain.tx_url(p["tx_hash"])
    e.add_field(name="Arc commit", value=f"[{p['tx_hash'][:10]}…]({tx})" if tx else "unanchored (hash stored)", inline=True)
    e.add_field(name="Hash", value=f"`{p['commit_hash'][:18]}…`", inline=True)
    if res.game:
        e.add_field(name="Market", value=f"[View on Gemini]({res.game.market_url})", inline=True)
    if rec and rec.tier in ("verified", "sharp") and p.get("venues_json"):
        lines = []
        for q in service.venues(p):
            star = " ⭐ cheapest" if q["cheapest"] else ""
            tag = " — grades this pick" if q["primary"] else ""
            price = fmt_pct(q["price"])
            lines.append(f"**{q['venue']}** {price}{tag}{star}" if q["primary"] else f"{q['venue']} {price}{star}")
        e.add_field(name="Where to tail", value="\n".join(lines), inline=False)
    e.set_footer(text=f"Graded on Gemini Predictions · {settings.public_url}/c/{capper['handle']}")
    return e


async def do_lock(message: discord.Message, text: str, choose_ticker: str | None = None, reply_to: discord.Message | None = None):
    capper = capper_for(message.author)
    res = await asyncio.to_thread(
        service.lock_pick, capper_id=capper["id"], source="discord", post_id=str(message.id), text=text,
        posted_at=discord_time(message.id), gemini=gemini, channel_id=str(message.channel.id),
        guild_id=str(message.guild.id) if message.guild else None, choose_ticker=choose_ticker)
    target = reply_to or message
    if res.choices:
        opts = res.choices[:5]
        lines = [f"{i + 1}. **{s.label}** in {g.title} (<t:{int(g.start_time.timestamp())}:f>)" for i, (g, s) in enumerate(opts)]
        msg = await target.reply(f"{res.message}\n" + "\n".join(lines) + "\nReply with the number.")
        pending_choices[msg.id] = {"origin": message.id, "channel": message.channel.id, "author": message.author.id,
                                   "text": text, "tickers": [g.ticker for g, _ in opts]}
        return
    if not res.ok:
        if res.message:
            await target.reply(res.message, mention_author=False)
        return
    if res.message == "Already locked.":
        return
    rec = verify.compute(capper["id"])
    if rec.tier in ("verified", "sharp") and res.game and res.side:
        quotes = await asyncio.to_thread(odds.compare, res.game, res.side)
        service.save_venues(res.pick["id"], odds.to_json(quotes))
        res.pick = dict(db.connect().execute("SELECT * FROM picks WHERE id=?", (res.pick["id"],)).fetchone())
    sent = await target.reply(embed=lock_embed(res, capper, rec), mention_author=False)
    service.set_reply(res.pick["id"], str(sent.id))


@client.event
async def on_ready():
    await tree.sync()
    background.start()
    log.info("Receipts bot ready as %s", client.user)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    ref = message.reference.message_id if message.reference else None
    if ref in pending_choices and message.content.strip().isdigit():
        ctx = pending_choices[ref]
        if ctx["author"] != message.author.id:
            return
        idx = int(message.content.strip()) - 1
        if 0 <= idx < len(ctx["tickers"]):
            pending_choices.pop(ref)
            origin = await message.channel.fetch_message(ctx["origin"])
            await do_lock(origin, ctx["text"], choose_ticker=ctx["tickers"][idx], reply_to=origin)
        return
    from .parse import is_pick_channel
    if is_pick_channel(getattr(message.channel, "name", "")):
        await do_lock(message, message.content)


@client.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent):
    content = payload.data.get("content")
    if content is None:
        return
    pick = service.flag("discord", str(payload.message_id), "edit", content)
    if not pick:
        return
    channel = client.get_channel(payload.channel_id)
    if channel:
        e = discord.Embed(title="Edited after lock", color=0xE5484D,
                          description=f"Original, as committed:\n> {pick['text']}\n\nNow says:\n> {content}")
        e.set_footer(text=f"Hash {pick['commit_hash'][:18]}… · this edit counts against verification")
        try:
            msg = await channel.fetch_message(payload.message_id)
            await msg.reply(embed=e, mention_author=False)
        except discord.HTTPException:
            await channel.send(embed=e)


@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    pick = service.flag("discord", str(payload.message_id), "delete")
    if not pick:
        return
    channel = client.get_channel(payload.channel_id)
    if channel:
        e = discord.Embed(title="Locked pick deleted", color=0xE5484D,
                          description=f"<@{pick['capper_id'].split(':')[1]}> deleted:\n> {pick['text']}\n"
                                      f"{pick['event_title']} · {fmt_pct(pick['lock_price'])} at lock")
        e.set_footer(text="It stays on the record. Deletes count against verification.")
        await channel.send(embed=e)


@tree.command(name="record", description="Show a capper's Gemini-graded record")
async def record_cmd(interaction: discord.Interaction, user: discord.Member | None = None):
    user = user or interaction.user
    capper = capper_for(user)
    rec = verify.compute(capper["id"])
    tier = {"sharp": "✅ Verified · Sharp", "verified": "✅ Verified", "unverified": "Not verified yet"}[rec.tier]
    e = discord.Embed(title=f"{capper['display_name']} — {tier}", color=GEMINI_GREEN,
                      url=f"{settings.public_url}/c/{capper['handle']}")
    e.add_field(name="Record", value=f"{rec.wins}-{rec.losses}" + (f" ({rec.voids} void)" if rec.voids else ""))
    e.add_field(name="Units at Gemini prices", value=f"{rec.units:+.2f}u")
    e.add_field(name="CLV", value=f"{rec.avg_clv * 100:+.1f} pts" if rec.avg_clv is not None else "n/a")
    e.add_field(name="Rules", value="\n".join(f"{'✅' if r.passed else '❌'} {r.label}: {r.actual}/{r.required}" for r in rec.rules), inline=False)
    e.set_footer(text=f"Graded on Gemini Predictions · last {settings.window_days} days" + (" · demo window" if settings.demo_mode else ""))
    await interaction.response.send_message(embed=e)


@tree.command(name="leaderboard", description="Top Gemini-graded cappers")
async def leaderboard_cmd(interaction: discord.Interaction):
    rows = verify.leaderboard()[:10]
    lines = [f"**{i + 1}. {r['display_name']}** {'✅' if r['verified'] else '·'} {r['wins']}-{r['losses']}, {r['units']:+.2f}u"
             for i, r in enumerate(rows)] or ["No picks yet."]
    e = discord.Embed(title="Receipts leaderboard", description="\n".join(lines), color=GEMINI_GREEN,
                      url=f"{settings.public_url}/")
    e.set_footer(text="Graded on Gemini Predictions")
    await interaction.response.send_message(embed=e)


async def x_text(url: str) -> str | None:
    """Free public oEmbed. Returns the post text, or None if blocked."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get("https://publish.twitter.com/oembed", params={"url": url, "omit_script": "1"})
            r.raise_for_status()
            m = re.search(r"<p[^>]*>(.*?)</p>", r.json().get("html", ""), re.S)
            return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else None
    except Exception:
        return None


@tree.command(name="import_x", description="Lock a moneyline you posted on X (only before the game starts)")
@app_commands.describe(url="Link to your X post", text="Paste the post text if the preview is blocked")
async def import_x_cmd(interaction: discord.Interaction, url: str, text: str | None = None):
    parsed = parse_x_url(url)
    if not parsed:
        await interaction.response.send_message("That isn't an X post link.", ephemeral=True)
        return
    await interaction.response.defer()
    handle, post_id = parsed
    body = await x_text(url) or text
    if not body:
        await interaction.followup.send("Couldn't read that post. Run it again with the `text` option.")
        return
    capper = capper_for(interaction.user)
    posted = x_time(post_id)
    # Closing the loophole: only games that haven't started *now* can be imported, so there are no winners to pick.
    games = [g for g in gemini.moneylines() if g.start_time and g.start_time > datetime.now(timezone.utc)]
    res = await asyncio.to_thread(service.lock_pick, capper_id=capper["id"], source="x", post_id=post_id, text=body,
                                  posted_at=posted, gemini=gemini, games=games)
    if not res.ok:
        await interaction.followup.send(res.message or "No moneyline found in that post. Imports only work before the game starts.")
        return
    db.connect().execute("UPDATE cappers SET x_handle=? WHERE id=?", (handle, capper["id"]))
    db.connect().commit()
    await interaction.followup.send(content=f"Imported from X (@{handle}), posted <t:{int(posted.timestamp())}:f> per the post ID.",
                                    embed=lock_embed(res, capper, None))


@tasks.loop(minutes=5)
async def background():
    await asyncio.to_thread(service.snapshot_closes, gemini)
    graded = await asyncio.to_thread(service.grade_pending, gemini)
    await asyncio.to_thread(service.anchor_pending)
    for p in graded:
        ch = client.get_channel(int(p["channel_id"])) if p.get("channel_id") else None
        if ch and p.get("reply_id"):
            icon = {"win": "✅ Win", "loss": "❌ Loss", "void": "Void"}[p["result"]]
            try:
                msg = await ch.fetch_message(int(p["reply_id"]))
                await msg.reply(f"{icon}: {p['side_label']} ML, graded on Gemini Predictions.", mention_author=False)
            except discord.HTTPException:
                pass
    await sync_roles()


async def sync_roles():
    for guild in client.guilds:
        role = discord.utils.get(guild.roles, name=settings.verified_role)
        if not role:
            continue
        for row in verify.leaderboard():
            if not row["id"].startswith("discord:"):
                continue
            member = guild.get_member(int(row["id"].split(":")[1]))
            if not member:
                continue
            try:
                if row["verified"] and role not in member.roles:
                    await member.add_roles(role, reason="Receipts: verification rules passed")
                elif not row["verified"] and role in member.roles:
                    await member.remove_roles(role, reason="Receipts: verification rules no longer pass")
            except discord.Forbidden:
                log.warning("Missing permission to manage %s in %s", settings.verified_role, guild.name)


def main():
    logging.basicConfig(level=logging.INFO)
    if not settings.discord_token:
        raise SystemExit("Set DISCORD_TOKEN in .env (free bot from https://discord.com/developers/applications).")
    client.run(settings.discord_token)


if __name__ == "__main__":
    main()
