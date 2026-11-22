# 0024: The fake adapter's camera sees what the simulator's sees

Status: Accepted · Date: 2026-11-22 · Milestone: M4.0

## Context
`FakePerceiver` answered `detect` with every object of the class in the scene, wherever it was. `SimPerceiver` sees an object only within 3 m of the robot and 40° either side of its heading. So the same plan gave different inspection results on the two adapters. The demo inspection round checks `present:red_box` at panel C, 6 m from the box: in MuJoCo the check fails and is reported as an anomaly, but on the fake adapter it passed. `absent:` checks go the other way: a toy 5 m behind the robot made `absent:toy` fail on the fake adapter and pass in MuJoCo. The fake adapter is the default, so a user's first run could report an anomaly that is not there, or miss one that is. The alternative was to document the difference, which would leave the default adapter giving answers no robot could give.

## Decision
- **One rule for what the head camera has in view.** `CameraView` (`spingi/perception/view.py`): a point is in view when it is within range (3 m) of the robot's real pose and inside the field of view (40° either side of its heading). Plane geometry, as before: height and occlusion play no part.
- **Both truth perceivers apply it.** `SimPerceiver` takes a `CameraView` instead of its `max_range_m` and `half_fov_rad` arguments, with the same values; landmarks keep their 4 m range. `FakePerceiver` takes the robot whose camera it is (anything with a `pose`). Given a robot, `detect` returns only the objects in view and `localize` only the markers in view. An object the scene does not place is never in view.
- **The session gives the fake perceiver the fake robot.** On the fake adapter, `spingi run`, `bench`, `plan --run` and `replay` see what the simulator would see from the same place.
- **Unit tests keep the perceiver that sees everything.** Built without a robot, `FakePerceiver` returns every configured object, as before, so the tests of detection, retry and escalation do not have to place the robot.

## Consequences
- **Same perception on both adapters.** A sim test runs the demo inspection round and the material runner on both adapters and compares every detection and every check. A session test runs the toy example on the fake adapter. `make demo` now reports the anomaly at panel C that `make demo-sim` has always reported.
- **Nothing changes in MuJoCo.** `SimPerceiver` computes the same thing as before, and the golden episodes, all recorded in MuJoCo, replay identically. An episode recorded earlier on the fake adapter may replay with fewer detections, because an object out of view is no longer seen.
- **Known remaining differences.** The fake perceiver does not follow objects that a plan picks and places: a carried box is still seen where it was. It ignores `--sigma`. Its false negatives drop a whole look instead of single objects. A plan that picks an object and looks for it again elsewhere can still give different results on the two adapters.
- **How it is checked.** Unit tests cover `CameraView` (range, field of view, a heading across ±180°) and `FakePerceiver` (with and without a robot, a view that follows the robot as it walks, false negatives). The two new perception tests fail with the old perceiver that sees everything.
