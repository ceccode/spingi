# AprilTag images

`tag36h11_NN.png` for ids 0 to 63: the 10x10-cell AprilTag (family 36h11) plus a one-cell white quiet zone on each side, 10 pixels per cell, 1-bit PNG. Generated once with OpenCV (`cv2.aruco.generateImageMarker`) and committed, so that scene generation needs no OpenCV and is deterministic.

The scene generator (`spingi/adapters/sim_mujoco/scene.py`) textures four thin decals with the tag of an object's `marker_id`; `MarkerPerceiver` (`spingi/perception/markers.py`) measures the black square, 10/12 of the decal side, which is 0.9 times the object's smallest extent. Print the same ids on real containers at the same rule, or set the size in the `MarkerSpec`.
