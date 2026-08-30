# SPDX-License-Identifier: MIT
# Regenerate the STLs.
#
#   make            - regenerate with the Python generator (no dependencies)
#   make openscad   - regenerate from the OpenSCAD sources (needs openscad)
#   make clean      - delete the generated STLs

STL_DIR := stl
SCAD    := openscad

CHASSIS_PARTS := chassis_base speaker_grille battery_tray
TRACK_PARTS   := track_frame drive_wheel idler_wheel track_belt
HEAD_PARTS    := head_shell eye_pod neck_bracket arm

.PHONY: all openscad clean check

all:
	python3 tools/gen_stl.py

openscad:
	@mkdir -p $(STL_DIR)
	@for p in $(CHASSIS_PARTS); do \
		echo "  scad  $$p"; \
		$(SCAD) -o $(STL_DIR)/$$p.stl -D "part=\"$$p\"" src/chassis.scad; \
	done
	@for p in $(TRACK_PARTS); do \
		echo "  scad  $$p"; \
		$(SCAD) -o $(STL_DIR)/$$p.stl -D "part=\"$$p\"" src/tracks.scad; \
	done
	@for p in $(HEAD_PARTS); do \
		echo "  scad  $$p"; \
		$(SCAD) -o $(STL_DIR)/$$p.stl -D "part=\"$$p\"" src/head.scad; \
	done

check:
	python3 -m pytest tools -q

clean:
	rm -f $(STL_DIR)/*.stl
