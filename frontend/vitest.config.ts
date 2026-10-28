import {defineConfig} from "vitest/config";
import {fileURLToPath} from "node:url";
import {dirname,resolve} from "node:path";
const root=dirname(fileURLToPath(import.meta.url));
export default defineConfig({esbuild:{jsx:"automatic"},test:{environment:"node",include:["src/**/*.test.{ts,tsx}"]},resolve:{alias:{"@":resolve(root,"src")}}});
