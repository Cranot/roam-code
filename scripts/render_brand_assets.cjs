// Optional authoring dependency: sharp. Rasterize committed SVGs, not new artwork.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('sharp');

async function main() {
  const directory = path.resolve(__dirname, '../templates/distribution/landing-page/brand');
  for (const name of ['roam-logo', 'roam-logo-mono', 'roam-logo-white', 'roam-mark']) {
    const width = name === 'roam-mark' ? 512 : 1600;
    const source = await fs.readFile(path.join(directory, `${name}.svg`));
    await sharp(source, { density: 600 }).resize({ width }).ensureAlpha().png().toFile(path.join(directory, `${name}.png`));
    console.log(`${name}.png: ${width}px wide, transparent RGBA`);
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
