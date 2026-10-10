const required = "26.7.0";
if (process.versions.node.localeCompare(required, undefined, { numeric: true }) < 0) {
  console.error(
    `node >= ${required} required (found ${
      process.versions.node
    }). Run "mise install" (repo ships .mise.toml) or "mise exec node@26.7.0 -- make check".`,
  );
  process.exit(1);
}
