// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title PickRegistry
/// @notice Append-only log of keccak commitments to sports picks. The hash covers the capper,
///         the post ID (which encodes the post time), the pick text, the Gemini market and the
///         Gemini price at lock. Anyone can recompute it from the public profile page.
contract PickRegistry {
    struct Commit {
        address committer;
        uint64 timestamp;
    }

    mapping(bytes32 => Commit) public commits;

    event PickCommitted(bytes32 indexed hash, address indexed committer, string pickRef, uint256 timestamp);

    error AlreadyCommitted(bytes32 hash);

    function commit(bytes32 hash, string calldata pickRef) external {
        if (commits[hash].timestamp != 0) revert AlreadyCommitted(hash);
        commits[hash] = Commit(msg.sender, uint64(block.timestamp));
        emit PickCommitted(hash, msg.sender, pickRef, block.timestamp);
    }

    function isCommitted(bytes32 hash) external view returns (bool, address, uint64) {
        Commit memory c = commits[hash];
        return (c.timestamp != 0, c.committer, c.timestamp);
    }
}
