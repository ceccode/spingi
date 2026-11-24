# Writing a scene

A scene is a YAML file: the world the robot knows. Check it with `validate_scene`, look at it with `preview_scene`, keep it with `save_scene`. An unknown key is an error, so a misspelled section cannot vanish in silence.

## Coordinates

- Metres on the ground plane: `x` to the right, `y` up on the map. Pick an origin (a corner of the building, the charging dock) and measure everything from it.
- Angles (`yaw`, `facing`) in radians, counter-clockwise from `+x`: `0` faces +x, `1.5708` faces +y, `3.1416` faces -x, `-1.5708` faces -y.
- Names of locations, objects and classes: letters, digits, `_` and `-`, at most 64 characters.

## A complete small scene

```yaml
# Stock room: a low shelf against the east wall, a bench by the door.
robot:
  pose: { x: 0.0, y: 0.0, yaw: 0.0 }      # where the robot starts
locations:
  dock:   { pose: { x: 0.0, y: 0.0, yaw: 0.0 } }
  rack_A: { pose: { x: 4.0, y: 2.0, yaw: 0.0 }, tolerance_m: 0.15 }   # 0.75 m in front of the shelf, facing it
  bench:  { pose: { x: 1.0, y: 4.2, yaw: 1.5708 } }
obstacles:
  - { x: 5.0, y: 2.0, w: 0.5, d: 3.0, h: 0.9 }      # a low shelf: centre, size along x, size along y, height
  - { x: 1.0, y: 5.2, w: 1.6, d: 0.8, h: 0.9, color: "#8a6f4d" }      # the bench
objects:
  crate_01: { cls: crate, marker_id: 7, size: [0.3, 0.2, 0.2], pose: { x: 4.85, y: 2.0, z: 1.0 } }   # on the shelf: 0.9 + half its height
routes:
  "dock->bench": []          # a straight line that crosses nothing
safety:
  geofence: { x_min: -1.0, x_max: 6.0, y_min: -1.0, y_max: 6.5 }
  max_speed: 0.8
```

## Sections

**`robot`**: `pose` where it starts (default the origin), `battery_pct` (default 100).

**`locations`**: the named places, the only places a plan can name. `pose: { x, y, yaw }`; `tolerance_m` (default 0.15) is how close counts as arrived. The `yaw` is where the robot faces once there, so it is where the camera looks.

**`obstacles`**: solid boxes standing on the ground, aligned with the axes: `x`, `y` (the centre), `w` (size along x), `d` (size along y), `h` (height), optional `color: "#rrggbb"`. No rotation: a slanted wall is a row of short boxes, an L-shaped building is two boxes. Nothing hangs in the air.

**`objects`**: what the camera looks for. No collision. `cls` is the class plans ask for (`present:crate`, `detect` with `cls: crate`); `pose: { x, y, z }` is the object's centre, so `z` is the height of what it stands on plus half its own height, and an object is on top of a box or in front of it, never inside one; `size: [along x, along y, height]` (default `[0.15, 0.1, 0.1]`); `marker_id` (0 to 63) prints an AprilTag on it, which is how the robot recognises it in `realistic` mode. An object without `pose` has an unknown position.

**`safety`**: `geofence: { x_min, x_max, y_min, y_max }` (leaving it is an emergency stop that ends the run), `max_speed` in m/s (a faster request is capped), `min_battery_pct` (default 5).

**`routes`**: `"from->to": [waypoints]`, the waypoints that keep that leg clear. A reminder for whoever writes plans; every name must be a location.

**`landmarks`**: AprilTags at known places, for `realistic` mode. See the `landmarks` guide.

**`terrain`**: the ground, outdoors only. Leave it out indoors.

```yaml
terrain:
  extent: { x_min: -3, x_max: 25, y_min: -8, y_max: 9 }   # must cover the geofence
  cell_m: 0.1                 # 0.08 to 0.25
  seed: 11                    # same seed, same bumps
  surface: asphalt            # floor, concrete, asphalt, gravel, dirt, grass
  regions:                    # rectangles of another surface; the last listed wins where two overlap
    - { surface: gravel, x: [3, 12], y: [-8, -2] }
  ramps:                      # a solid slope, at most 45 degrees
    - { x: [12.5, 16.0], y: [3.5, 5.5], from_m: 0.0, to_m: 0.45, along: x }
  raised:                     # flat on top, vertical sides: a kerb, a step, a loading dock; 0 to 3 m
    - { x: [-3, 14], y: [5.5, 9], height_m: 0.10 }
```

Not supported: pits, ditches, water, overhangs, rotated boxes.

## What makes a scene work

- **The robot walks in straight lines.** There is no path planner. For every leg, picture the line between its two points: if it crosses a box, add a location to go around it and use it as a waypoint. Keep 0.7 to 1 m free on either side for a walking robot.
- **A stop is where the robot looks from.** The camera reads an object up to 3 m away, within 40 degrees either side of the heading. Put an inspection stop 1 to 3 m from what it checks, with the `yaw` that faces it. The Go2's camera is 30 cm above the ground: what it inspects should be under half a metre high. `pick` needs the object within 0.9 m.
- **Put objects where a camera can see them**: on top of a box or in front of it. Inside a box they are hidden, and only the simulator's ground truth still finds them.
- **Stand clear of what you look at.** A stop 0.3 m or more from a table or a rack is fine. Inside a box, or touching it, is a mistake.
- **Stay inside the fence**, by 0.3 m or more: a robot that overshoots a place at the edge is stopped.
- **Outdoors, meet a kerb square on**: a waypoint in front of it, then straight up.

## From a floor plan

1. **Scale.** Measure a known length on the plan (a wall, a door) in pixels: that gives pixels per metre.
2. **Origin.** A corner of the building; `x` to the right, `y` up. Convert every point: metres from that corner.
3. **Walls and furniture** become boxes: centre, size along x, size along y, height. Model only what stands in the robot's way or in its camera's view.
4. **Places.** One location where the robot starts, one per task (in front of each thing to check or fetch, facing it), one wherever a straight line would hit something.
5. **Fence.** The rectangle the robot must not leave, a little inside the walls.
6. **Check and look.** `validate_scene`, then `preview_scene`: compare the picture with the floor plan, with the person if you can.
7. **Try it.** `save_scene`, then a plan that visits every location with `run_plan` in `kinematic` mode.

A model of a home or a workplace is personal data: keep it, and the episodes made in it, out of public places.
