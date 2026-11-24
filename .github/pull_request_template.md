## What changes

<!-- What is true after this pull request, in a sentence or two. Link the issue if there is one. -->

## How it was checked

<!-- The commands you ran and what they showed. An episode .zip or a screenshot of the viewer when behaviour changed. -->

## Checklist

- [ ] `make test` and `make lint` pass in `runtime/` (and `npm run lint`, `npm test`, `npm run build` in `viewer/` if it changed)
- [ ] New or changed modules have their tests
- [ ] READMEs, tutorial and spec still describe what the code does
- [ ] A change of decision comes with an ADR, its row in `adr/README.md` and the range in the root README
- [ ] No safety layer is weakened, and nothing gives a model a way to move the robot outside a validated plan
