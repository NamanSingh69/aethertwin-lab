# Working on AetherTwin

Use Python 3.12. Follow README for the hash-locked Windows or Ubuntu CPU setup.
The Pi is a separate capture target; never install the x86 lock on it.

```sh
python -m pytest -q
cd web
npm ci
npm test
npm run build
```

Keep the built `web/dist/` files with frontend changes: the Python service serves
those files without requiring Node at runtime. Preserve the exact bytes of
`SPEC.md` and retain corrections in documentation rather than rewriting history.

Keep simulator labels outside runtime sensor frames. Unknown space remains
blocked for navigation; live startup errors must not silently enable simulation.
Record processing resolutions, seeds, source/checkpoint hashes and source mode
in benchmarks. Do not describe CPU, synthetic or historical results as physical
Pi/A2000 measurements. Read `docs/PROJECT_STATUS.md` before deployment work.

Run `python tools/repository_manifest.py --check` to verify a clone. After an
intentional change, stage the intended files with Git, run
`python tools/repository_manifest.py`, then stage the updated manifest.

Do not commit credentials, TLS private keys, real lab recordings without review,
Python environments or node_modules. Use the gateway `.env.example` as a template
outside version control. Third-party binaries retain their included notices;
this private repository does not introduce a new open-source licence grant.
