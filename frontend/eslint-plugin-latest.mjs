import sarj from "@sarj/eslint-plugin";

import primaryExportFileName from "./eslint-rules/primary-export-file-name.mjs";

export default {
  ...sarj,
  rules: {
    ...sarj.rules,
    "primary-export-file-name": primaryExportFileName,
  },
};
