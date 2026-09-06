#!/usr/bin/env python3
"""Compatibility entrypoint for the authenticated FastAPI control plane."""

from control_plane.app import main


if __name__ == "__main__":
    main()
