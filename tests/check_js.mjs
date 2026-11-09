// Синтаксис Worker и встроенного скрипта приложения (без запуска).
import { readFileSync } from "node:fs";
const html = readFileSync(new URL("../worker/src/app.html", import.meta.url), "utf8");
const js = html.split("<script>").pop().split("</script>")[0];
new Function(js);
for (const fn of ["cleanupNodes", "removeMany", "updateMany", "aboutNodes", "usageTag"]) {
  if (!js.includes(`function ${fn}(`)) throw new Error(`app.html: пропала функция ${fn}`);
}
console.log("app.html ok");
