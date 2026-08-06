# Demo

Narrated walkthrough of the Regulatory Affairs KG on a fast, real subset.

```bash
python -m demo.demo
```
Record/regenerate:
```bash
asciinema rec --overwrite --cols 92 --rows 32 --idle-time-limit 2.0 \
  -c "bash -c 'python -m demo.demo'" demo/regulatory-affairs.cast
agg demo/regulatory-affairs.cast demo/regulatory-affairs.gif
```
