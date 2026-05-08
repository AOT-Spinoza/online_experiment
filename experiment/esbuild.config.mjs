// Minimal esbuild config for the AoT experiment.
//
//   node esbuild.config.mjs build  — one-shot production build into dist/
//   node esbuild.config.mjs dev    — watch + local dev server on :3000
//
// Bundles src/main.js (and any CSS imported from it, including jsPsych's
// stylesheet) into dist/. Static assets (index.html, anything under public/)
// are copied verbatim into dist/ on build and on every rebuild in dev mode.
//
// Kept deliberately tiny so the build chain is auditable — no Vite, no Rollup,
// no React. esbuild does one job (turn JS+CSS into a bundle) and one only.

import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';

const mode = process.argv[2] || 'build';
const isDev = mode === 'dev';

function copyStatic() {
  fs.mkdirSync('dist', { recursive: true });
  fs.copyFileSync('index.html', 'dist/index.html');
  if (fs.existsSync('public')) {
    for (const entry of fs.readdirSync('public')) {
      const src = path.join('public', entry);
      const dst = path.join('dist', entry);
      // Use lstat so symlinks are detected as such (don't follow them).
      // We support three cases under public/:
      //   - regular files: copy verbatim
      //   - symlinks: recreate with an absolute target in dist/ so they
      //     resolve regardless of where dist/ is served from. This is how
      //     dev_link.py's `_videos` symlink to pipeline/staging_hashed/
      //     becomes accessible at http://localhost:3000/_videos/<hash>.mp4
      //   - regular directories: skipped (we don't expect any in v1)
      const lst = fs.lstatSync(src);
      if (lst.isSymbolicLink()) {
        const target = fs.readlinkSync(src);
        const absoluteTarget = path.resolve(path.dirname(src), target);
        try { fs.rmSync(dst, { recursive: true, force: true }); } catch {}
        fs.symlinkSync(absoluteTarget, dst);
      } else if (lst.isFile()) {
        fs.copyFileSync(src, dst);
      }
    }
  }
}

const buildOptions = {
  entryPoints: ['src/main.js'],
  bundle: true,
  outfile: 'dist/experiment.bundle.js',
  format: 'esm',
  // es2022 lets us use top-level `await` and other modern features. All
  // browsers we require (Chrome 89+, Firefox 89+, Safari 15+) support this.
  target: ['es2022'],
  sourcemap: true,
  minify: !isDev,
  logLevel: 'info',
  loader: { '.css': 'css' },
};

copyStatic();

if (isDev) {
  // Re-copy static assets after every esbuild rebuild so HTML/CSS edits show up.
  const ctx = await esbuild.context({
    ...buildOptions,
    plugins: [
      {
        name: 'copy-static-on-rebuild',
        setup(build) {
          build.onEnd(() => copyStatic());
        },
      },
    ],
  });
  await ctx.watch();
  const { port } = await ctx.serve({ servedir: 'dist', port: 3000 });
  console.log(`\n  dev server: http://localhost:${port}/\n  ctrl-c to stop\n`);
} else {
  await esbuild.build(buildOptions);
  console.log('\n  built to dist/\n');
}
