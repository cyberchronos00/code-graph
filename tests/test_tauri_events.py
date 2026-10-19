"""Tauri events and Svelte components (#111): `emit('x')` on one side and `listen('x', ..)` on the other link through
endpoint:tauri-event:x; `invoke` / `listen` inside a .svelte component (its <script> blocks and on* handlers) have a
sender / receiver."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import bridges as B  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

HAS_TS = (ROOT / "cg_code_graph" / "plugins" / "ts" / "extractor" / "node_modules").exists()

LIB = """\
use tauri::{Emitter, Listener, Manager};

#[tauri::command]
fn save_note(text: String) -> usize { text.len() }

fn on_sync(event: tauri::Event) { let _ = event.payload(); }

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            app.listen("note-saved", move |_event| {
                handle.emit("notes-changed", 1).unwrap();
            });
            app.listen_any("sync-now", on_sync);
            app.listen("tauri://focus", |_e| {});
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![save_note])
        .run(tauri::generate_context!())
        .expect("run");
}
"""

PANEL = """\
<script lang="ts">
  import { invoke } from '@tauri-apps/api/core'
  import { emit, listen } from '@tauri-apps/api/event'
  import { onMount } from 'svelte'

  let count = 0
  function onChanged() { count += 1 }

  onMount(async () => {
    await listen('notes-changed', onChanged)
  })

  async function save() {
    await invoke('save_note', { text: 'hello' })
    // emit('commented-out')
    await emit('note-saved', 'tauri://not-a-comment')
  }
</script>

<button onclick={save}>Save</button>
<button on:click={() => emit('sync-now')}>Sync</button>
<p>{count}</p>
"""


FILES = {
    "package.json": '{"name": "notes", "dependencies": {"@tauri-apps/api": "^2.0.0", "svelte": "^5.0.0"}}',
        "tsconfig.json": '{"compilerOptions": {"strict": true}, "include": ["src"]}',
        "src/Panel.svelte": PANEL,
        "src/main.ts": "import Panel from './Panel.svelte'\nexport const app = Panel\n",
        "src-tauri/Cargo.toml": '[package]\nname = "notes"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\ntauri = "2"\n',
        "src-tauri/src/lib.rs": LIB,
}


def _write(tmp_path, files):
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


@pytest.mark.skipif(not HAS_TS, reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
def test_tauri_events_and_svelte(tmp_path):
    _write(tmp_path, FILES)
    st = index_project(tmp_path, tmp_path / "n.db", "notes")
    g = GraphStore(tmp_path / "n.db")
    eps = {e["namespace"]: e for e in B.bridges(g, protocol="tauri-event")["endpoints"]}
    assert set(eps) == {"note-saved", "notes-changed", "sync-now", "tauri://focus"}
    comp = "component:src/Panel.svelte"
    # webview -> core, sent from the component's <script>, received by the function registering the listener
    ns = eps["note-saved"]
    assert [(s["fn"], s["at"], s["process"]) for s in ns["senders"]] == [(comp, "src/Panel.svelte:16", "webview")]
    assert [(r["handler"], r["process"]) for r in ns["receivers"]] == [("function:notes::run", "core")]
    # core -> webview: emitted inside the Rust listener closure
    nc = eps["notes-changed"]
    assert [s["fn"] for s in nc["senders"]] == ["function:notes::run"]
    assert [r["handler"] for r in nc["receivers"]] == [comp]
    # an on:click handler in the template, received by a named Rust callback
    sn = eps["sync-now"]
    assert [s["at"] for s in sn["senders"]] == ["src/Panel.svelte:21"]
    assert [r["handler"] for r in sn["receivers"]] == ["function:notes::on_sync"]
    # Tauri's own event: no sender in the repo, no check
    assert not eps["tauri://focus"]["checks"] and not eps["tauri://focus"]["senders"]
    assert st["bridges"]["tauri"]["event_sends"] == 3 and st["bridges"]["tauri"]["event_listeners"] == 4
    # invoke from the Svelte component reaches the command
    cmd = {e["namespace"]: e for e in B.bridges(g, protocol="tauri")["endpoints"]}["save_note"]
    assert [s["fn"] for s in cmd["senders"]] == [comp]
    assert [r["handler"] for r in cmd["receivers"]] == ["function:notes::save_note"]


@pytest.mark.skipif(not HAS_TS, reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
def test_declared_process_roles_win(tmp_path):
    _write(tmp_path, {**FILES, "src/util.ts": "export const one = 1\n",
                      ".cg.yaml": "platforms:\n  processes:\n    'src/**': renderer\n"})
    index_project(tmp_path, tmp_path / "n.db", "notes")
    g = GraphStore(tmp_path / "n.db")
    eps = {e["namespace"]: e for e in B.bridges(g, protocol="tauri-event")["endpoints"]}
    assert [s["process"] for s in eps["note-saved"]["senders"]] == ["renderer"]
    assert [r["process"] for r in eps["note-saved"]["receivers"]] == ["core"]      # inferred, not declared
    rows = {r["id"]: r["attrs"] for r in g.q(
        "SELECT id, attrs FROM nodes WHERE id IN ('module:src/util.ts', 'component:src/Panel.svelte')")}
    for nid in ("module:src/util.ts", "component:src/Panel.svelte"):
        a = json.loads(rows[nid])
        assert a["process"] == "renderer" and a["process_from"] == "config"
