# 0019: Biome formats and lints the viewer

Status: Accepted · Date: 2026-11-13 · Milestone: M4.0

## Context
The runtime has ruff in CI for lint and format. The viewer had only `tsc` and its tests: nothing checked formatting, import order or common mistakes, and fifteen lines were longer than 160 characters. After ADR-0018 the viewer has ten source files, and without a formatter each of them drifts toward the style of whoever edited it last. The choice was between ESLint with Prettier (two tools, several plugins and configuration files) and Biome (one binary, one file).

## Decision
- **Biome, one pinned version.** `@biomejs/biome` 2.6.7 is an exact dev dependency of the viewer. `biome.json` formats and lints `src/`, `index.html` and the configuration files: 2 spaces, 120 columns like the runtime, double quotes, semicolons, imports sorted, the recommended rules.
- **One rule turned off.** `style/noNonNullAssertion` is off. The viewer compiles with `noUncheckedIndexedAccess`, so `array[i]!` inside a loop over that array's own length is the accepted way to say the index is in range.
- **Checked in CI.** `npm run lint` (`biome check .`) runs in the viewer job before the tests and fails on a formatting difference or a lint error. `npm run format` applies the fixes.

## Consequences
- **One large diff, once.** Every source file of the viewer was reformatted in the commit that adds Biome; `git blame` on those lines points at it. No logic changed in that commit apart from the two findings below.
- **Two findings fixed.** The play button gets `type="button"`, and an assignment inside an expression in `panels.ts` becomes a statement.
- **Upgrades are deliberate.** A new Biome version can change formatting, so it is upgraded on purpose, with `biome migrate` and a reformat in the same commit.
- **Not covered.** Biome does not type-check: `tsc --noEmit` stays in `npm run build`. The runtime keeps ruff.
