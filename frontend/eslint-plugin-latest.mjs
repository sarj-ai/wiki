import sarj from "@sarj/eslint-plugin";

import primaryExportFileName from "./eslint-rules/primary-export-file-name.mjs";

const latestSarjPlugin = {
  ...sarj,
  rules: {
    ...sarj.rules,
    "primary-export-file-name": primaryExportFileName,
  },
};

export default latestSarjPlugin;
