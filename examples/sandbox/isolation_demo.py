"""Harmless observations; run explicitly through sandbox.cli, never as a malware test."""
import json
import os
import socket

observations = {"cwd": os.getcwd(), "home": os.environ.get("HOME")}
for label, path in (("host_account_file", "/etc/passwd"), ("host_secrets", "/home/jitendra/.ssh/id_rsa")):
    try:
        with open(path, "rb") as handle:
            handle.read(1)
        observations[label] = "unexpectedly readable"
    except OSError as error:
        observations[label] = f"denied or absent ({type(error).__name__})"
try:
    with open("/usr/ai_os_demo_probe", "w") as handle:
        handle.write("harmless probe")
    observations["protected_write"] = "unexpectedly allowed"
except OSError as error:
    observations["protected_write"] = f"denied ({type(error).__name__})"
try:
    # Reserved documentation address; no application data is sent.
    with socket.create_connection(("192.0.2.1", 9), timeout=0.5):
        observations["network_connection"] = "unexpectedly connected"
except OSError as error:
    observations["network_connection"] = f"unavailable ({type(error).__name__})"
with open("/tmp/demo-output.txt", "w") as handle:
    handle.write("temporary sandbox output")
observations["temporary_write"] = "allowed; discarded at exit"
print(json.dumps(observations, indent=2))
