// SVG -> PNG at the sheet's natural size, deviceScaleFactor 2.
//
// Playwright is resolved from `web/node_modules` rather than imported by bare
// specifier: this script lives in docs/, which has no package.json, so a bare
// import would fail no matter where it is run from.
import { readFileSync } from 'node:fs'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const pw = resolve(here, '../../../web/node_modules/@playwright/test/index.mjs')
const { chromium } = await import(pathToFileURL(pw).href)

const browser = await chromium.launch()
for (const svgPath of process.argv.slice(2)) {
  const src = readFileSync(svgPath, 'utf8')
  const w = Number(/width="(\d+)"/.exec(src)[1])
  const h = Number(/height="(\d+)"/.exec(src)[1])
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2 })
  await page.goto(pathToFileURL(svgPath).href)
  await page.screenshot({ path: svgPath.replace(/\.svg$/, '.png'), clip: { x: 0, y: 0, width: w, height: h } })
  await page.close()
  console.log('rendered', svgPath.split('/').pop(), `${w}x${h}`)
}
await browser.close()
