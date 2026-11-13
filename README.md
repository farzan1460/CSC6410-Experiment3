# CSC6410-Experiment3
Python-based network configuration validation and drift detection for Windows

# Network Configuration Validation and Drift Detection

This project demonstrates automated network configuration collection, JSON Schema validation, and configuration drift detection on a Windows computer using Python and PowerShell.

# Objective

The purpose of this experiment is to determine whether automated network configuration checking can be faster and more reliable than manual inspection.

Files

network_config_checker.py — Collects network information, validates the observed configuration, and detects configuration drift.

host_model.yang — Course-provided YANG model describing host network configuration.

host_schema.json — JSON Schema used to validate collected network data.

intended.json — Expected network configuration.

observed_redacted.json — Redacted sample of the actual collected network configuration.


Experiment 3 — Python, JSON, YANG-like Models, and Configuration Drift Detection
