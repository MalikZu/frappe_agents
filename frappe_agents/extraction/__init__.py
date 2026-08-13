"""Document extraction: a file becomes a reviewed draft, and never more than that.

The package is four steps in a line:

* `schema.py` turns a DocType into a JSON schema both provider families accept.
* `resolve.py` turns extracted text into candidate Link values, proposing only.
* `gate.py` decides what a human has to confirm before it can be written.
* `pipeline.py` runs the job and creates the draft — **minus every sensitive field**.

The rule the whole package exists to enforce: a sensitive value extracted from a
document is never written by the pipeline. It lives on the Document Extraction row
until a person names it in `apply_extraction`'s confirmed list.
"""
