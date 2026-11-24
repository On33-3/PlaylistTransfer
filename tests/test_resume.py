"""Retomar una transferencia que se cortó, y que cada espera se avise (pedido del usuario, 2026-11-10:
Spotify pidió una pausa con ~1600 temas y volver a empezar repetía todas las búsquedas)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.connectors.base import ConnectorError, clock_after, human_wait
from app.core.models import Song
from app.storage import progress as progress_mod
from app.storage.progress import SearchProgress
from tests.ui_helpers import make_api, open_page


def pausing(yt, after: int):
    """El destino corta (como Spotify pidiendo horas de pausa) después de `after` búsquedas, una sola vez."""
    real, searched = yt.search, []

    def search(song):
        if len(searched) == after and not getattr(search, "fired", False):
            search.fired = True
            raise ConnectorError("Spotify pidió una pausa de 3 h. Se puede seguir desde las 18:40.")
        searched.append(song.title)
        return real(song)
    yt.search = search
    return searched


def run(api, dry=False):
    r = api.start_transfer("tidal", "ytmusic", "src", [{"name": "Para correr", "id": "dst"}], dry)
    return api._jobs.wait(r["job"], 10)


def kinds(done, kind):
    return [e for e in done["events"] if e["type"] == kind]


def test_a_transfer_that_stops_picks_up_where_it_left(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    searched = pausing(yt, after=2)
    first = run(api)
    assert first["state"] == "error" and "3 h" in first["error"]
    assert kinds(first, "saved") == [{"type": "saved", "done": 2, "total": 4}]     # se dice que quedó guardado
    assert yt.calls == []                                     # se cortó buscando: no se tocó ninguna playlist
    assert list((tmp_path / "busquedas").glob("*.json"))

    searched.clear()
    second = run(api)
    assert second["state"] == "done"
    assert kinds(second, "resumed") == [{"type": "resumed", "done": 2, "total": 4}]  # se dice que sigue
    assert searched == ["Hallelujah", "Bohemian Rhapsody"]    # los dos primeros no se buscaron otra vez
    cached = [e["n"] for e in kinds(second, "track") if e["cached"]]
    assert cached == [1, 2]
    assert second["result"]["summary"] == clean_run(tmp_path)["result"]["summary"]   # mismo resultado
    assert not list((tmp_path / "busquedas").glob("*.json"))  # terminó bien: no queda nada guardado


def clean_run(tmp_path):
    api, *_ = make_api(tmp_path / "limpia")
    return run(api)


def test_a_test_run_leaves_its_searches_for_the_real_one(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    searched = pausing(yt, after=99)
    assert run(api, dry=True)["state"] == "done" and len(searched) == 4
    searched.clear()
    real = run(api)
    assert real["state"] == "done" and searched == [] and kinds(real, "resumed")[0]["done"] == 4


def test_cancelling_also_keeps_what_was_searched(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    real = yt.search

    def search(song):
        if song.title == "Hallelujah":
            api._jobs.busy().cancel.set()
        return real(song)
    yt.search = search
    done = run(api)
    assert done["state"] == "cancelled" and kinds(done, "saved")[0]["done"] == 3


def test_old_or_broken_progress_is_ignored(tmp_path):
    p = SearchProgress(tmp_path, "tidal", "src", "ytmusic")
    song = Song("Tema", ["Artista"], duration=200, id="t1")
    p.put(song, Song("Tema", ["Artista"], id="y1"), 0.9, [])
    p.save()
    assert SearchProgress(tmp_path, "tidal", "src", "ytmusic").get(song)[0].id == "y1"
    assert SearchProgress(tmp_path, "tidal", "otra", "ytmusic").get(song) is None     # otra playlist: otro archivo
    data = json.loads(p.path.read_text(encoding="utf-8"))
    data["saved"] = (datetime.now() - timedelta(days=progress_mod.KEEP_DAYS + 1)).isoformat()
    p.path.write_text(json.dumps(data), encoding="utf-8")
    assert SearchProgress(tmp_path, "tidal", "src", "ytmusic").get(song) is None      # vencido
    p.path.write_text("{roto", encoding="utf-8")
    assert SearchProgress(tmp_path, "tidal", "src", "ytmusic").get(song) is None      # roto: no impide transferir


def test_waits_are_said_in_words_and_with_the_time_they_end():
    assert [human_wait(s) for s in (45, 61, 3600, 12000)] == ["45 s", "2 min", "1 h", "3 h 20 min"]
    now = datetime(2026, 11, 10, 15, 20)
    assert clock_after(3 * 3600 + 20 * 60, now) == "18:40"
    assert clock_after(20 * 3600, now) == "11/11 11:20"                                   # otro día: con fecha


# ---------------------------------------------------------------- en la pantalla

def test_the_screen_says_it_was_saved_and_then_that_it_resumes(browser, tmp_path):
    from tests.ui_helpers import check, shot
    api, tidal, yt = make_api(tmp_path)
    pausing(yt, after=2)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    page.click("#go")
    page.wait_for_selector("#done.show")
    detail = page.inner_text("#done-detail")
    assert "3 h" in detail and "Lo buscado quedó guardado (2 de 4 temas)" in detail and "YouTube Music" in detail
    assert "Encontrado, sin agregar" in page.inner_text("#run .legend")    # no se agregó nada: no dice "Agregado"
    assert page.locator("#tracks .badge.now, #mosaic i.now").count() == 0  # nada queda "buscando"
    shot(page, "transferir-cortada-guardada")
    page.click("#again")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    page.evaluate("""() => { const real = onTransferEvent; window.notes = [];     // lo que dice mientras corre
        onTransferEvent = (tr, e) => { real(tr, e);
          const n = document.querySelector('#run-note'); if (!n.hidden) notes.push(n.textContent); }; }""")
    page.click("#go")
    page.wait_for_selector("#done.show")
    assert "Sigue donde había quedado: 2 de 4 temas ya estaban buscados." in page.evaluate("notes")
    assert check(page, "transferir") == [] and errors == []


def test_a_pause_shows_a_countdown(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.evaluate("""() => { state.transfer = { total: 10, cached: 0, pausedMs: 0, n: {}, rows: {}, tracks: {} };
        document.querySelector('#run').hidden = false;
        onTransferEvent(state.transfer, { type: 'waiting', service: 'Spotify', why: 'rate', seconds: 160 }); }""")
    assert page.inner_text("#eta") == "Spotify pidió una pausa: sigo en 2:40."
    page.evaluate("onTransferEvent(state.transfer, { type: 'waiting', service: 'YouTube Music', why: 'retry', seconds: 5 })")
    assert page.inner_text("#eta") == "YouTube Music no contestó bien: pruebo de nuevo en 5 s."
    page.evaluate("onTransferEvent(state.transfer, { type: 'waited' })")
    assert page.is_hidden("#eta") and errors == []


# ---------------------------------------------------------------- mientras se agrega

def test_cancel_does_not_cut_the_writing_halfway(tmp_path, monkeypatch):
    """Desde que se empieza a agregar, una pausa del servicio se avisa pero Cancelar ya no la corta:
    la playlist quedaría a medias y la pantalla diría "no se modificó ninguna playlist"."""
    monkeypatch.setattr("app.connectors.base.time.sleep", lambda s: None)
    api, tidal, yt = make_api(tmp_path)
    real = yt.add

    def add(pid, songs):
        api._jobs.busy().cancel.set()            # la persona toca Cancelar justo cuando se agrega
        yt.pause(1)                              # y el servicio pide una pausa
        real(pid, songs)
    yt.add = add
    done = run(api)
    assert done["state"] == "done" and [s.id for s in yt.songs("dst")] == ["y2old", "y1"]    # se agregó entero
    types = [e["type"] for e in done["events"]]
    assert types.index("writing") < types.index("adding_to") < types.index("waiting")


def test_the_screen_says_how_many_were_added_and_how_long_is_left(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    texts = page.evaluate("""() => { const tr = state.transfer = { total: 3, cached: 0, pausedMs: 0, n: {}, rows: {}, tracks: {} };
        document.querySelector('#run').hidden = false; document.querySelector('#run-actions').hidden = false;
        onTransferEvent(tr, { type: 'writing' });
        const cancelHidden = document.querySelector('#run-actions').hidden;
        onTransferEvent(tr, { type: 'adding_to', name: 'Me gusta', total: 1600 });
        tr.adding.started = -60000;                                  // va 1 minuto
        showAdding(tr, 400, 1600, 0);
        return [cancelHidden, document.querySelector('#eta').textContent]; }""")
    assert texts == [True, "Agregando a «Me gusta»: 400 de 1600 · faltan alrededor de 3 minutos"]
    assert errors == []
