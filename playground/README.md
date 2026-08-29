# raghealth playground

Interactive industry demos of RAG knowledge-base rot. No database, no
credentials — everything runs on synthetic scenario data.

## Deploy free

**Streamlit Community Cloud** (recommended): New app → this repo →
main file `playground/app.py`. Add `raghealth` to requirements via the
repo itself: replace the `raghealth` line in `playground/requirements.txt`
with `git+https://github.com/vkk1978/raghealth.git` until it's on PyPI.

**Hugging Face Spaces**: create a Streamlit Space, copy `playground/` and
the `raghealth/` package, done.

## Static demo gallery (GitHub Pages)

`python playground/build_static.py` renders every scenario to
`docs/` as self-contained HTML. Enable Pages on the `docs/` folder and you
have a zero-cost demo site.
