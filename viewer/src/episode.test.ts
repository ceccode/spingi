import { strToU8 } from "fflate";
import { describe, expect, it } from "vitest";
import { describeEvent, manifestRows } from "./describe";
import {
  cameraVideoOf,
  checkEvents,
  checkTrajectory,
  DEFAULT_ROBOT_MODEL,
  type Event,
  landmarksOf,
  locationsOf,
  MAX_SCENE_ENTRIES,
  type Manifest,
  normalizePaths,
  objectsOf,
  obstaclesOf,
  parseEpisodeFiles,
  parseJsonl,
  robotJointsUrl,
  robotModelUrl,
  type Sample,
  type Scene,
} from "./episode";
import { eventTime } from "./timeline";

describe("robotModelUrl", () => {
  it("maps the robot profile of the manifest to a bundled model", () => {
    expect(robotModelUrl("unitree_g1")).toBe("/models/g1.glb");
    expect(robotModelUrl("unitree_go2")).toBe("/models/go2.glb");
  });
  it("never builds a path from the episode's text: unknown or odd names fall back to the default", () => {
    for (const bad of ["spot", "../../etc/passwd", "constructor", "__proto__", "", undefined, null, 7, {}]) {
      expect(robotModelUrl(bad)).toBe(DEFAULT_ROBOT_MODEL);
    }
  });
});

describe("parsing", () => {
  it("reads jsonl ignoring blank lines", () => {
    expect(parseJsonl<{ a: number }>('{"a":1}\n\n{"a":2}\n')).toEqual([{ a: 1 }, { a: 2 }]);
  });
  it("strips the top-level folder", () => {
    const files = normalizePaths({
      "ep-1/manifest.json": strToU8("{}"),
      "ep-1/frames/a.png": strToU8("x"),
      "ep-1/frames/": new Uint8Array(),
    });
    expect(Object.keys(files).sort()).toEqual(["frames/a.png", "manifest.json"]);
  });
  it("rejects unknown versions", () => {
    const files = { "manifest.json": strToU8(JSON.stringify({ format_version: "9.9" })) };
    expect(() => parseEpisodeFiles(files)).toThrow(/not supported/);
  });
  it("builds a minimal episode", () => {
    const manifest = {
      format_version: "0.1",
      run_id: "r",
      created_at: "",
      robot: { model: "fake", adapter: "fake" },
      plan_id: "p",
      status: "success",
      steps_completed: 1,
      steps_total: 1,
      duration_s: 0,
      files: [],
    };
    const files = {
      "manifest.json": strToU8(JSON.stringify(manifest)),
      "scene.yaml": strToU8("locations:\n  dock: { pose: { x: 0, y: 0, yaw: 0 } }\n"),
      "plan.yaml": strToU8("id: p\nsteps:\n  - skill: say\n    params: { text: hi }\n"),
      "events.jsonl": strToU8(
        '{"ts": 10, "run_id": "r", "kind": "run.start"}\n{"ts": 12.5, "run_id": "r", "kind": "run.end", "status": "success"}\n',
      ),
      "trajectory.jsonl": strToU8('{"t": 0, "robot": {"x": 0, "y": 0, "yaw": 0, "mode": "idle"}}\n'),
    };
    const ep = parseEpisodeFiles(files);
    expect(ep.scene.locations?.dock?.pose.x).toBe(0);
    expect(ep.plan.steps[0]?.skill).toBe("say");
    expect(eventTime(ep.events[1]!, ep.events)).toBeCloseTo(2.5);
    expect(describeEvent(ep.events[1]!)).toBe("run end: success");
  });
});

describe("physics episodes", () => {
  const walk: Sample[] = [
    {
      t: 0,
      robot: { x: 0, y: 0, yaw: 0, mode: "walking", z: 0.3, roll: 0, pitch: 0.1 },
      joints: { knee: 0.2, hip: 1.0 },
    },
    {
      t: 1,
      robot: { x: 1, y: 0, yaw: 0, mode: "walking", z: 0.5, roll: 0.2, pitch: 0.3 },
      joints: { knee: 0.6, hip: 1.0 },
    },
  ];
  it("refuses non-finite heights and joint angles, and too many joints", () => {
    const bad = (s: Partial<Sample>): Sample[] => [{ ...walk[0]!, ...s } as Sample];
    expect(() => checkTrajectory(bad({ robot: { ...walk[0]!.robot, z: NaN } }))).toThrow(/malformed/);
    expect(() => checkTrajectory(bad({ joints: { knee: Infinity } }))).toThrow(/malformed/);
    const many = Object.fromEntries(Array.from({ length: 65 }, (_, i) => [`j${i}`, 0]));
    expect(() => checkTrajectory(bad({ joints: many }))).toThrow(/malformed/);
    expect(checkTrajectory(walk)).toHaveLength(2);
  });
  it("finds the joint table next to the model", () => {
    expect(robotJointsUrl(robotModelUrl("unitree_go2"))).toBe("/models/go2.joints.json");
  });
});

describe("what the robot believed (drifting odometry)", () => {
  const drift: Sample[] = [
    { t: 0, robot: { x: 0, y: 0, yaw: 0, mode: "idle" } },
    { t: 1, robot: { x: 1, y: 0, yaw: 0, mode: "walking" }, estimate: { x: 1, y: 0.2, yaw: 0.1 } },
    { t: 2, robot: { x: 2, y: 0, yaw: 0, mode: "walking" }, estimate: { x: 2, y: 0.4, yaw: 0.2 } },
  ];
  it("refuses a believed pose that is not three numbers", () => {
    expect(() => checkTrajectory([{ ...drift[1]!, estimate: { x: 1, y: NaN, yaw: 0 } }])).toThrow(/malformed/);
    expect(() => checkTrajectory([{ ...drift[1]!, estimate: "here" as unknown as Sample["estimate"] }])).toThrow(
      /malformed/,
    );
  });
  it("reads the scene's landmarks with their defaults and skips malformed ones", () => {
    const lms = landmarksOf({
      landmarks: [
        { marker_id: 40, x: 2.6, y: -1.3, facing: 2.68 },
        { marker_id: 41, x: 1, y: 1, facing: 0, size_m: 0.3, height_m: 0.45, post: false },
        { marker_id: "x", x: 0, y: 0 },
        { marker_id: 42, x: "1", y: 0 },
        { marker_id: 43, x: 0, y: 0, size_m: 50 },
        null,
      ],
    });
    expect(lms.map((l) => l.markerId)).toEqual([40, 41]);
    expect(lms[0]).toMatchObject({ height: 0.6, size: 0.2, post: true });
    expect(lms[1]).toMatchObject({ height: 0.45, size: 0.3, post: false });
    expect(landmarksOf({ landmarks: "nope" })).toEqual([]);
  });
  it("shows JPEG frames from a noisy camera as well as PNG ones", () => {
    const manifest = {
      format_version: "0.1",
      run_id: "r",
      created_at: "",
      robot: { model: "unitree_go2", adapter: "sim_mujoco" },
      plan_id: "p",
      status: "success",
      steps_completed: 0,
      steps_total: 0,
      duration_s: 0,
      files: [],
    };
    const ep = parseEpisodeFiles({
      "manifest.json": strToU8(JSON.stringify(manifest)),
      "scene.yaml": strToU8("{}"),
      "plan.yaml": strToU8("id: p\nsteps: []\n"),
      "events.jsonl": strToU8(""),
      "frames/sim-1.jpg": strToU8("j"),
      "frames/sim-2.png": strToU8("p"),
      "frames/notes.txt": strToU8("t"),
    });
    expect([...ep.frames.keys()].sort()).toEqual(["sim-1", "sim-2"]);
    expect(ep.frames.get("sim-1")!.type).toBe("image/jpeg");
  });
});

describe("the head camera's video", () => {
  const entry = { file: "head.mp4", camera: "head", fps: 10, start_t: 0.1, frames: 1084, width: 640, height: 480 };
  const manifest = (video: unknown) => ({ camera_video: video }) as unknown as Manifest;
  const files = { "head.mp4": strToU8("mp4") };
  it("reads the entry and the video it names", () => {
    const v = cameraVideoOf(manifest(entry), files)!;
    expect(v.blob.type).toBe("video/mp4");
    expect([v.camera, v.fps, v.start, v.frames, v.width, v.height]).toEqual(["head", 10, 0.1, 1084, 640, 480]);
  });
  it("has none when the manifest names none, or the archive left the file out", () => {
    expect(cameraVideoOf(manifest(undefined), files)).toBeNull();
    expect(cameraVideoOf(manifest(entry), {})).toBeNull();
  });
  it("refuses a path for a file name, and counts or rates that are not numbers it can play", () => {
    for (const bad of [
      { file: "../head.mp4" },
      { file: "frames/x.mp4" },
      { file: "head.webm" },
      { fps: 0 },
      { fps: "10" },
      { start_t: -1 },
      { frames: 0 },
      { frames: 1.5 },
      { width: 1e6 },
      { camera: 7 },
    ]) {
      expect(() => cameraVideoOf(manifest({ ...entry, ...bad }), files)).toThrow(/camera_video/);
    }
    expect(() => cameraVideoOf(manifest("head.mp4"), files)).toThrow(/camera_video/);
  });
});

describe("the scene of an episode from a stranger", () => {
  const scene = (raw: unknown): Scene => raw as Scene;
  it("reads the named places with their tolerance", () => {
    const places = locationsOf(
      scene({
        locations: {
          dock: { pose: { x: 1, y: 2, yaw: 0.5 } },
          gate: { pose: { x: 3, y: 4, yaw: 0 }, tolerance_m: 0.3 },
        },
      }),
    );
    expect(places).toEqual([
      { name: "dock", x: 1, y: 2, yaw: 0.5, tolerance: 0 },
      { name: "gate", x: 3, y: 4, yaw: 0, tolerance: 0.3 },
    ]);
  });
  it("skips a place without a pose, or with a pose or a tolerance that is not numbers", () => {
    const places = locationsOf(
      scene({
        locations: {
          ok: { pose: { x: 0, y: 0, yaw: 0 } },
          bare: {},
          text: "here",
          none: null,
          half: { pose: { x: 1, y: 2 } },
          words: { pose: { x: "1", y: 2, yaw: 0 } },
          wide: { pose: { x: 0, y: 0, yaw: 0 }, tolerance_m: "wide" },
        },
      }),
    );
    expect(places.map((p) => p.name)).toEqual(["ok"]);
  });
  it("reads the obstacles with their default size, and keeps each one's place in the list when one is skipped", () => {
    const obstacles = obstaclesOf(
      scene({ obstacles: [{ x: 1, y: 2 }, { x: "far" }, null, { x: 3, y: 4, w: 2, d: 1, h: 0.5, color: "#12abEF" }] }),
    );
    expect(obstacles).toEqual([
      { index: 0, x: 1, y: 2, w: 0.5, d: 0.5, h: 1, color: null },
      { index: 3, x: 3, y: 4, w: 2, d: 1, h: 0.5, color: "#12abEF" },
    ]);
  });
  it("draws an obstacle in the default colour when its colour is not a hex colour, and skips one with a size that is not a number", () => {
    expect(obstaclesOf(scene({ obstacles: [{ x: 0, y: 0, color: "red; background: url(x)" }] }))[0]?.color).toBeNull();
    expect(obstaclesOf(scene({ obstacles: [{ x: 0, y: 0, color: 7 }] }))[0]?.color).toBeNull();
    expect(
      obstaclesOf(
        scene({
          obstacles: [
            { x: 0, y: 0, h: "tall" },
            { x: 0, y: 0, w: -1 },
          ],
        }),
      ),
    ).toEqual([]);
  });
  it("reads the objects, resting on the floor at the origin when the scene gives no pose", () => {
    const objects = objectsOf(
      scene({
        objects: {
          box: { cls: "red_box", pose: { x: 1, y: 2, z: 0.9 } },
          crate: { cls: "crate", size: [0.4, 0.3, 0.2] },
        },
      }),
    );
    expect(objects).toEqual([
      { id: "box", x: 1, y: 2, z: 0.9, size: [0.15, 0.1, 0.1] },
      { id: "crate", x: 0, y: 0, z: 0.1, size: [0.4, 0.3, 0.2] },
    ]);
  });
  it("skips an object whose pose or size is not numbers", () => {
    const objects = objectsOf(
      scene({
        objects: {
          a: null,
          b: { pose: { x: 1 } },
          c: { size: [1, 2] },
          d: { size: "big" },
          e: { pose: "there" },
          ok: {},
        },
      }),
    );
    expect(objects.map((o) => o.id)).toEqual(["ok"]);
  });
  it("has nothing to draw when the scene's lists are missing or of the wrong kind", () => {
    for (const odd of [
      {},
      { locations: [1, 2], obstacles: { a: 1 }, objects: "many" },
      { locations: null, obstacles: null, objects: null },
    ]) {
      expect(locationsOf(scene(odd))).toEqual([]);
      expect(obstaclesOf(scene(odd))).toEqual([]);
      expect(objectsOf(scene(odd))).toEqual([]);
    }
  });
  it("draws no more than a bounded number of each", () => {
    const many = Array.from({ length: MAX_SCENE_ENTRIES + 50 }, (_, i) => i);
    const pose = { x: 0, y: 0, yaw: 0 };
    expect(locationsOf(scene({ locations: Object.fromEntries(many.map((i) => [`p${i}`, { pose }])) }))).toHaveLength(
      MAX_SCENE_ENTRIES,
    );
    expect(obstaclesOf(scene({ obstacles: many.map(() => ({ x: 0, y: 0 })) }))).toHaveLength(MAX_SCENE_ENTRIES);
    expect(objectsOf(scene({ objects: Object.fromEntries(many.map((i) => [`o${i}`, {}])) }))).toHaveLength(
      MAX_SCENE_ENTRIES,
    );
  });
});

describe("the events of an episode from a stranger", () => {
  const base = { ts: 0, run_id: "r" };
  it("are refused when a line is not an event: no kind, no time, or not an object", () => {
    expect(
      checkEvents([
        { ...base, kind: "run.start" },
        { ...base, kind: "say", sim_t: 2 },
      ]),
    ).toHaveLength(2);
    for (const bad of [
      null,
      5,
      "run.start",
      [],
      { ts: 0 },
      { ts: 0, kind: 7 },
      { kind: "say" },
      { ts: "now", kind: "say" },
      { ts: 0, kind: "say", sim_t: Infinity },
    ]) {
      expect(() => checkEvents([{ ...base, kind: "run.start" }, bad as unknown as Event])).toThrow(
        /malformed events line 2/,
      );
    }
  });
});

describe("an episode whose scene or plan is not what the format says", () => {
  const manifest = {
    format_version: "0.1",
    run_id: "r",
    created_at: "",
    robot: { model: "fake", adapter: "fake" },
    plan_id: "p",
    status: "success",
    steps_completed: 0,
    steps_total: 0,
    duration_s: 0,
    files: [],
  };
  const files = (
    scene: string,
    plan: string,
    events = '{"ts": 1, "run_id": "r", "kind": "run.start"}\n',
  ): Record<string, Uint8Array> => ({
    "manifest.json": strToU8(JSON.stringify(manifest)),
    "scene.yaml": strToU8(scene),
    "plan.yaml": strToU8(plan),
    "events.jsonl": strToU8(events),
  });
  it("is read with an empty scene and an unnamed plan when those files hold no mapping", () => {
    for (const odd of ["", "just text\n", "- a\n- b\n", "42\n"]) {
      const ep = parseEpisodeFiles(files(odd, odd));
      expect(ep.scene).toEqual({});
      expect(ep.plan).toEqual({});
      expect(Object.fromEntries(manifestRows(ep)).Plan).toBe("");
    }
  });
  it("is refused when its event log has a line that is not an event", () => {
    expect(() => parseEpisodeFiles(files("{}", "{}", '{"ts": 1, "run_id": "r", "kind": "run.start"}\n7\n'))).toThrow(
      /malformed events line 2/,
    );
  });
  it("is refused when a trajectory line gives an object a position that is not three numbers", () => {
    const robot = { x: 0, y: 0, yaw: 0, mode: "idle" };
    expect(checkTrajectory([{ t: 0, robot, objects: { box: { x: 1, y: 2, z: 3 } } }])).toHaveLength(1);
    for (const bad of [{ box: null }, { box: { x: 1, y: 2 } }, "boxes", [1], { box: { x: 1, y: 2, z: "up" } }]) {
      expect(() => checkTrajectory([{ t: 0, robot, objects: bad } as unknown as Sample])).toThrow(
        /malformed trajectory line 1/,
      );
    }
  });
});
