Per-layer schema validation before merge (#5110 Slice 3).

Configuration layers across run overlays and the resolution cascade (session
environment variables such as `BERNSTEIN_MAX_AGENTS` and `BERNSTEIN_EFFORT`,
project config, context, and global settings) are now validated against
dedicated section schemas and top-level Field constraints before merge.
Validation scope covers single-layer section schemas and field constraints;
cross-field validation (`_validate_cross_fields`) is out of scope for layer checks.
Invalid values raise `LayerValidationError` and CLI commands surface the error
cleanly with the layer name and source path.

