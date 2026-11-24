# Landmarks

A walking robot does not know exactly where it is. In `realistic` mode its pose estimate drifts by a few per cent of the distance walked, more on gravel and dirt. A landmark is an AprilTag at a known place: when the camera reads one, `navigate` corrects the pose and walks the difference. Without landmarks a long route ends up metres off: the robot stops short, walks off a ramp, or inspects the wrong spot and reports something missing that is there.

Landmarks matter only in `realistic` mode. In `fake`, `kinematic` and `physics` the robot knows its pose exactly.

```yaml
landmarks:
  - { marker_id: 41, x: 13.2, y: 0.7, facing: -2.642, size_m: 0.3 }                       # on a post
  - { marker_id: 48, x: 6.55, y: 7.49, facing: -1.5708, height_m: 0.45, size_m: 0.3, post: false }   # flat on a wall
```

- `marker_id`: 0 to 63, one tag per id in the scene, objects' tags included.
- `x`, `y`: where the tag is. `facing`: the direction its face looks, in radians. A tag faces the robot that reads it: for a robot coming along `+x`, the tag ahead of it faces `-x` (`3.1416`).
- `size_m`: the side of the black square (default 0.2, at most 0.6). `height_m`: its centre above the ground (default 0.6, at most 3).
- `post: false`: flat on a wall or an obstacle, no post drawn. Landmarks have no collision: keep them off the routes anyway.

## When the robot looks

Before it leaves, at every waypoint and at the destination. It corrects its pose with the first tag it reads and, if the correction puts it off the waypoint, walks the difference and looks again.

## Where to put them

- **A tag is read up to 10 times its side away**: 3 m for a 30 cm tag, 2 m for the default 20 cm. Use 30 cm tags outdoors.
- **In the camera's view**: within 45 degrees of where the robot faces at that place, and seen from the front, not edge on (within 75 degrees of the tag's own facing).
- **One 2 to 3 m ahead of every stop**, seen at an angle of 20 to 35 degrees rather than straight ahead.
- **Two, one either side, of an inspection target** the robot stands close to: arriving 30 cm off to one side, it still sees one.
- **One every 8 to 10 m on a long leg**: add a waypoint with a tag ahead of it. Drift grows with the distance, and half a metre of error puts the robot off a ramp.
- **Low for the Go2**: its camera is 30 cm above the ground, so `height_m: 0.45` or lower reads better than a tag at eye level.

`validate_scene` warns about a stop that looks at an object and has no landmark to read. `preview_scene` draws each tag with an arrow for its facing: the arrow should point towards where the robot stands when it reads it.

## Checking that they work

Run the plan in `realistic` mode. The result's metrics give the corrections (`relocalizations`), the tags expected and not seen (`landmarks_missed`) and the worst gap between where the robot was and where it believed it was (`pose_error_max_m`). `get_events` with kinds `robot.relocalized` and `localization` lists each reading: the tag, the place, the size of the correction, or the tags that were expected and missed. Then `bench_plan` in `realistic` mode: a route that succeeds every time with its landmarks is one you can trust.
