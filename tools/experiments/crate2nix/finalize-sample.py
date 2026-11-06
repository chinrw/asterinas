#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Retain the job's complete validation result beside its compilation timing."""

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("metrics", type=Path)
parser.add_argument("status")
args = parser.parse_args()
metrics = json.loads(args.metrics.read_text())
passed = args.status == "success"
metrics["job_checks_passed"] = passed
metrics["validation_passed"] = metrics.get("validation_passed", False) and passed
metrics["valid_performance_sample"] = metrics.get("valid_performance_sample", False) and passed
args.metrics.write_text(json.dumps(metrics, indent=2) + "\n")
