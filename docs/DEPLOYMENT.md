# Deployment guide

The app is one Docker image. It runs on Hugging Face Spaces, on Render, or on any machine with
Docker. Nothing in this guide has been deployed for you: each section lists the steps.

## What the container needs

| Setting (environment variable) | Meaning | Default |
|---|---|---|
| `PORT` | Port the app listens on | `7860` |
| `GMS_DATA_DIR` | Folder for the feedback database and the lookup caches | `/data` in the image |
| `GMS_VLM_BASE_URL` | OpenAI-compatible address of the extraction model, for example `https://my-server.example/v1`. Empty = PDF extraction is not offered | empty |
| `GMS_VLM_MODEL` | Model name that the server expects | `Qwen/Qwen2.5-VL-7B-Instruct` |
| `GMS_VLM_API_KEY` | Key of the model server | `EMPTY` |
| `GMS_ADMIN_TOKEN` | Token for the **Feedback data** download page. Empty = the page is switched off | empty |
| `GMS_OFFLINE` | `1` = never call PubChem (bundled hazard data and caches only) | off |
| `GMS_MAX_UPLOAD_MB` | Largest PDF the app accepts for extraction | `25` |
| `GMS_MAX_PDF_PAGES` | Most pages read in one extraction | `80` |

These settings are read on the server only. A visitor cannot see or change them. Set them as
"secrets" or "environment variables" of the platform, never in the repository.

`/data` holds the feedback you want to collect. **Give it persistent storage**, or download the
feedback regularly, because a container without a volume loses its files when it restarts.

Outbound network: the app calls `pubchem.ncbi.nlm.nih.gov` for compounds that are not in the
bundled data, and your extraction server if one is set. It calls nothing else.

## Run it locally

```bash
docker build -t green-metrics-studio .
```

```bash
docker run -p 7860:7860 -v gms-data:/data green-metrics-studio
```

With the extraction server and the feedback download:

```bash
docker run -p 7860:7860 -v gms-data:/data \
  -e GMS_VLM_BASE_URL=https://my-server.example/v1 \
  -e GMS_VLM_API_KEY=change-me \
  -e GMS_ADMIN_TOKEN=choose-a-long-token \
  green-metrics-studio
```

## Hugging Face Spaces

1. Create a new Space. Choose **Docker** as the SDK and the blank template.
2. Add this block at the very top of the `README.md` **in the Space repository** (Spaces reads
   its settings from it):

   ```yaml
   ---
   title: Green Metrics Studio
   emoji: 🌱
   colorFrom: green
   colorTo: gray
   sdk: docker
   app_port: 7860
   ---
   ```

3. Push this repository to the Space (`git remote add space https://huggingface.co/spaces/<user>/<space>`,
   then `git push space <branch>:main`).
4. In **Settings → Variables and secrets**, add `GMS_ADMIN_TOKEN` as a secret. Add
   `GMS_VLM_BASE_URL`, `GMS_VLM_API_KEY` and `GMS_VLM_MODEL` (the key as a secret) when you have
   an extraction server.
5. Feedback storage: a free Space has no persistent disk. Either add **Persistent storage** in
   the Space settings and set the variable `GMS_DATA_DIR=/data` (the storage is mounted at
   `/data`), or download the feedback often from the **Feedback data** page.
6. The image is about 1.7 GB (Java for name parsing, RDKit). The first build takes several minutes.

## Render

1. New → **Web Service** → connect the GitHub repository. Render detects the `Dockerfile`.
2. Instance type: at least 1 GB of memory (the free 512 MB instance is too small for RDKit,
   Java and Streamlit together).
3. Render sets `PORT` itself; the container uses it. Health check path: `/_stcore/health`.
4. Add the environment variables from the table above under **Environment**.
5. Add a **Disk** mounted at `/data` to keep the feedback.

## A virtual machine (or any Docker host)

1. Install Docker. Copy the repository to the machine and build the image as above, or build
   it elsewhere and push it to a registry.
2. Run it so that it restarts by itself:

   ```bash
   docker run -d --name gms --restart unless-stopped -p 127.0.0.1:7860:7860 \
     -v /srv/gms-data:/data --env-file /srv/gms.env green-metrics-studio
   ```

   `/srv/gms.env` holds the settings, one `NAME=value` per line. Keep it readable by root only.
   The folder `/srv/gms-data` must be writable by user id 1000 (`chown 1000 /srv/gms-data`).
3. Put a reverse proxy with HTTPS in front (Caddy, nginx or Traefik). Streamlit uses a
   WebSocket: the proxy must pass `Upgrade` and `Connection` headers. With Caddy this one line
   is enough: `gms.example.org { reverse_proxy 127.0.0.1:7860 }`.
4. Upload size: if the proxy limits request bodies (nginx: `client_max_body_size`), set it to
   at least `GMS_MAX_UPLOAD_MB`.

## Pointing the app at your Qwen server

The app talks to any OpenAI-compatible server. Start the model, for example on a Colab A100 or
a GPU server:

```bash
vllm serve Qwen/Qwen2.5-VL-7B-Instruct --max-model-len 16384 --limit-mm-per-prompt image=1 --api-key change-me
```

Then make the server reachable from the app:

- **GPU server with a public address:** put it behind HTTPS and set
  `GMS_VLM_BASE_URL=https://<host>/v1`.
- **Colab:** Colab has no public address. Open a tunnel from the notebook (for example
  `cloudflared tunnel --url http://localhost:8000` or ngrok) and set `GMS_VLM_BASE_URL` to the
  tunnel address plus `/v1`. The address changes each session: update the variable and restart
  the app (on Spaces and Render a changed variable restarts it).
- Always set an API key on the model server (`--api-key`) and the same value in
  `GMS_VLM_API_KEY`. Without a key, anyone who finds the address can use your GPU.

How the app behaves:

- It asks `GET <base>/models` with a 4-second limit, at most once every 30 seconds.
- If the server answers, the **Reaction** page offers the PDF upload.
- If the server does not answer, the page says that extraction is offline, which is normal, and
  offers a **Check again** button. Manual entry, every calculation, the diagnosis, the
  alternatives and the exports work without it.
- If the server refuses the key (401 or 403), the page asks the user to tell the administrator.
- The endpoint and the key never appear in the page, in a report or in an error message.

To test the connection before you deploy: `curl -H "Authorization: Bearer change-me" https://<host>/v1/models`.

## Security notes

- Streamlit's XSRF and CORS protection are on (`.streamlit/config.toml`). Do not switch them off.
- The container runs as a normal user (uid 1000), not as root.
- Uploaded PDFs are held in memory for the session, sent only to your extraction server, and
  not written to disk.
- The feedback download needs `GMS_ADMIN_TOKEN`. Choose a long random value.
- The app has no user accounts. Anyone who can reach it can use it. If it must be private, put
  it behind the access control of your platform or reverse proxy.
- Rate limiting is not built in. On a public address, use the platform's or the proxy's limits
  if misuse becomes a problem.

## Logs and errors

The app logs to standard output (`docker logs gms`, or the log view of the platform). A page
error shows a short message to the user and the full traceback in the log. There is no external
error tracking in this version.

## Updating the bundled data

- Hazard data for common compounds: `python scripts/build_hazard_seed.py` (needs PubChem).
- Precedents: `python scripts/build_precedents.py download --raw data/ord_raw`, then
  `python scripts/build_precedents.py build --raw data/ord_raw` (needs `requirements-data.txt`
  and about 1 GB of disk).
- After either, run `python scripts/run_benchmarks.py` and the tests, and rebuild the image.
