// The newsprint theme. `astryx experimental theme editor` wrote this file once and never changes it again.
// The checked settings live in newsprint.settings.json and the generated newsprint.settings.ts;
// change them with `astryx experimental theme editor set newsprint ...`. Add your own changes below: they win over the settings.
import {defineTheme} from '@astryxdesign/core/theme';
import {newsprintSettings} from './newsprint.settings';

export const newsprintTheme = defineTheme({
  extends: newsprintSettings,
  name: 'newsprint',
});
