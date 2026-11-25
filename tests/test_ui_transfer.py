"""Transfer: picking, live, result and the "?" decided at the end."""
from __future__ import annotations

from tests.fakes import S
from tests.ui_helpers import make_api, open_page, shot, transfer, doubtful_setup, run_live


def test_transfer_live_then_add_another_artist(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    assert page.inner_text("#go") == "Transferir 4 temas"
    shot(page, "3-transferir-elegir")
    page.click("#go")
    page.wait_for_selector("#done.show")
    assert page.inner_text("#count") == "4"
    assert [page.inner_text(f"#n-{k}") for k in ("ok", "doubt", "miss", "dup")] == ["1", "1", "1", "1"]
    done = page.inner_text("#done")
    assert "«Para correr»" in done and "agregaste 1 tema; 1 ya estaba en otra versión" in done
    tracks = page.inner_text("#run")                                # list and "To review"
    assert "No está esa versión" in tracks and "Jeff Buckley. No está" in tracks and "+ Hallelujah · Leonard Cohen" in tracks and "Ya estaba como «Mr. Blue Sky - 2012 Remaster»" in tracks
    shot(page, "4-transferir-resultado")
    page.click("[data-alt='3:0']")
    page.wait_for_selector("text=Agregada la de Leonard Cohen")
    assert [s.id for s in yt.songs("dst")] == ["y2old", "y1", "y3"]          # the live one (?) wasn't added on its own
    # The screen says how it ended up: it moved to "added", and another one can't be picked for the same song
    assert [page.inner_text(f"#n-{k}") for k in ("ok", "doubt", "miss", "dup")] == ["2", "1", "0", "1"]
    assert "agregaste 2 temas" in page.inner_text("#done")
    assert "ok" in page.get_attribute("#mosaic i:nth-child(3)", "class")
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    page.click("#hist button.row")
    page.wait_for_selector("#hist li.open .detail .badge")
    assert "Hallelujah" in page.inner_text("#hist")
    shot(page, "5-historial")
    assert errors == []


def test_dry_run_from_the_window_changes_nothing(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("#dst-new")
    assert page.input_value("#dst-new-name") == "Favoritas"
    page.click("#dry")
    page.wait_for_selector("#done.show")
    assert "no se modificó nada" in page.inner_text("#done")
    assert page.is_hidden("#open-dest") and yt.calls == []
    assert page.is_disabled("[data-alt='3:0']")
    assert errors == []


def test_transfer_screen_stays_simple(browser, tmp_path):
    """No extra checkboxes: neither a per-transfer report nor "add the ones that may be another version"."""
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=src]")
    assert page.locator("#pick input[type=checkbox]:not([name=dst]):not(#dst-new)").count() == 0
    transfer(page)
    page.wait_for_timeout(300)
    assert api._asked == []                                           # a file is never saved on its own
    assert errors == []


def test_more_tiles_than_fit_show_plus_n(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    tidal.add_playlist("big", "Grande", [S(f"Tema {i}", "Alguien", id=f"b{i}") for i in range(65)])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=big]")
    page.check("input[name=dst][value=dst]")
    page.click("#go")
    page.wait_for_selector("#done.show")
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist .mosaic i")
    assert page.locator("#hist li[data-op] >> nth=0").locator(".mosaic i").count() == 60
    assert page.inner_text("#hist li[data-op] >> nth=0 >> .more") == "+5 más"
    assert errors == []


def test_clicking_a_big_tile_jumps_to_that_song(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    tile = page.locator("#mosaic i >> nth=2")                         # Hallelujah
    assert tile.get_attribute("title").startswith("Hallelujah — Jeff Buckley: No encontrado. Tocá")
    tile.click()
    row = page.locator("#run .tracks li", has_text="Hallelujah")
    assert "flash" in row.get_attribute("class")
    assert errors == []


def test_lists_same_height_search_and_new_on_top(browser, tmp_path):
    """The screenshot's case: few source playlists and many destination ones."""
    api, tidal, yt = make_api(tmp_path)
    for i in range(14):
        yt.add_playlist(f"extra{i}", f"Lista {i:02d}", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=dst][value=extra13]")
    heights = page.evaluate("[...document.querySelectorAll('.picker ul')].map(u => Math.round(u.getBoundingClientRect().height))")
    assert heights[0] == heights[1], heights
    tops = page.evaluate("[...document.querySelectorAll('.picker ul')].map(u => Math.round(u.getBoundingClientRect().top))")
    assert tops[0] == tops[1], f"las cajas no quedan alineadas: {tops}"   # a search box in both, even if one is short
    first = page.locator("#dst-list li >> nth=0")
    assert "Crear una playlist nueva" in first.inner_text()
    page.fill(".picker:nth-child(2) .filter", "lista 1")
    visible = page.evaluate("[...document.querySelectorAll('#dst-list li[data-filter]')].filter(li => !li.hidden).map(li => li.querySelector('.name').textContent)")
    assert visible == ["Lista 10", "Lista 11", "Lista 12", "Lista 13"]          # "Lista 01" doesn't contain "lista 1"
    page.fill(".picker:nth-child(2) .filter", "no existe")
    assert page.is_visible("text=Ninguna playlist coincide.")
    page.fill(".picker:nth-child(2) .filter", "")
    shot(page, "12-listas-iguales")
    assert errors == []


def test_filter_does_not_lose_the_selection(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    for i in range(12):
        yt.add_playlist(f"extra{i}", f"Lista {i:02d}", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=extra3]")
    page.fill(".picker:nth-child(2) .filter", "lista 07")
    page.check("input[name=dst][value=extra7]")
    page.fill(".picker:nth-child(2) .filter", "")
    assert page.is_checked("input[name=dst][value=extra3]") and page.is_checked("input[name=dst][value=extra7]")
    assert not page.is_disabled("#go")
    assert errors == []


def test_a_preview_says_would_be_added(browser, tmp_path):
    """In "Try without changing anything" nothing is added: the legend, the notes and the squares say so."""
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page, dry=True)
    legend = page.inner_text("#run .legend")
    assert "✓ Se agregaría" in legend and "? No se agregaría, puede ser otra versión" in legend
    assert "Agregado" not in legend
    assert "No se agregaría «Bohemian Rhapsody (Live Aid 1985)» de Queen" in page.inner_text("#run")
    assert "1 tema no se agregaría porque puede ser otra versión" in page.inner_text("#done")
    assert page.locator("#mosaic i >> nth=0").get_attribute("title").startswith("Take On Me — a-ha: Se agregaría.")
    page.click("#again")                                             # a real transfer afterwards
    transfer(page)
    assert "✓ Agregado" in page.inner_text("#run .legend")
    assert "No se agregó «Bohemian Rhapsody (Live Aid 1985)» de Queen" in page.inner_text("#run")
    assert "1 tema no se agregó porque puede ser otra versión" in page.inner_text("#done")
    assert errors == []


def test_doubtful_are_decided_at_the_end_one_by_one(browser, tmp_path):
    api, tidal, yt = doubtful_setup(tmp_path)
    page, errors = open_page(browser, api)
    run_live(page)
    assert "? No agregado, puede ser otra versión" in page.inner_text("#run .legend")
    assert "No se agregó «Bohemian Rhapsody (Live Aid 1985)» de Queen" in page.inner_text("#run")
    assert "2 temas no se agregaron porque pueden ser otra versión" in page.inner_text("#done")
    assert sorted(s.id for s in yt.songs("dst")) == ["y1", "y2old"]          # only Take On Me
    assert page.inner_text("#add-all") == "Agregar los 2 que pueden ser otra versión"
    shot(page, "13-agregar-igual")
    page.click("#run button[data-add-doubtful='1']")                 # just one
    page.wait_for_selector("#run button[data-add-doubtful='1'].done")
    assert page.inner_text("#run button[data-add-doubtful='1']") == "✓ Agregado"
    assert "y4" in [s.id for s in yt.songs("dst")]
    assert page.inner_text("#add-all") == "Agregar el que puede ser otra versión"
    page.click("#add-all")                                              # the rest, all at once
    page.wait_for_selector("#add-all[hidden]", state="attached")
    assert "ylayla" in [s.id for s in yt.songs("dst")]
    assert errors == []


def test_preview_has_no_add_buttons(browser, tmp_path):
    api, *_ = doubtful_setup(tmp_path)
    page, errors = open_page(browser, api)
    run_live(page, dry=True)
    assert "? No se agregaría, puede ser otra versión" in page.inner_text("#run .legend")
    assert page.locator("#run [data-add-doubtful]").count() == 0 and page.is_hidden("#add-all")
    assert errors == []


def test_empty_playlists_are_not_offered_as_source(browser, tmp_path):
    """As a source, only those with something to move; as a destination, any (an empty one works)."""
    api, tidal, yt = make_api(tmp_path)
    tidal.add_playlist("vacia", "Para después", [])
    yt.add_playlist("ydvacia", "Recién creada", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=src][value=src]")
    assert page.locator("input[name=src][value=vacia]").count() == 0
    assert page.locator("input[name=dst][value=ydvacia]").count() == 1
    assert errors == []


def test_with_only_empty_playlists_the_source_list_says_so(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    tidal.playlists = {}
    tidal.add_playlist("vacia", "Para después", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("#src-list li.loading:not(:has-text('Cargando'))")
    assert page.inner_text("#src-list") == "Tus playlists de Tidal están vacías: no hay nada para pasar."
    assert errors == []


def test_a_song_repeated_in_the_source_has_its_own_square(browser, tmp_path):
    """The first user asked for it: a song repeated in the source playlist came out as "✓ Added"
    (and was added only once). Now it has its own square, color and number in the legend, live
    and in the history."""
    from tests.ui_helpers import check
    api, tidal, yt = make_api(tmp_path)
    name, songs_ = tidal.playlists["src"]
    tidal.add_playlist("src", name, songs_ + [S("Take On Me", "a-ha", dur=225, id="t1-otra-vez")])
    page, errors = open_page(browser, api)
    transfer(page)
    assert page.inner_text("#n-rep") == "1"
    assert page.locator("#mosaic i.rep").count() == 1 and page.locator("#tracks .badge.rep").count() == 1
    assert page.inner_text("#tracks .badge.rep") == "×2"
    row = page.locator("#tracks li", has=page.locator(".badge.rep")).inner_text()
    assert "Repetido más arriba en la playlist de origen" in row
    assert "Un tema estaba repetido en la playlist de origen" in page.inner_text("#done-detail")
    assert check(page, "transferir") == []
    shot(page, "transferir-repetido")
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op] .mosaic i.rep", state="attached")
    assert "Repetido en el origen" in page.inner_text("#hist-legend")
    assert errors == []


def test_conflicts_come_first_to_review(browser, tmp_path):
    """The first user asked for it: the songs waiting for a decision (a "?" to add anyway, or another
    artist's version) move up to "To review", below the result and in playlist order;
    they're resolved from there. In a dry run there's nothing to decide: it doesn't show."""
    from tests.ui_helpers import check
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    assert page.is_visible("#review")
    titles = page.eval_on_selector_all("#review-list .t", "ts => ts.map(t => t.textContent)")
    assert titles == ["Hallelujah", "Bohemian Rhapsody"]            # ✗ with another artist and ?, in order
    assert page.locator("#tracks [data-alt], #tracks [data-add-doubtful]").count() == 0
    review_top = page.locator("#review").bounding_box()["y"]
    assert page.locator("#done").bounding_box()["y"] < review_top < page.locator("#tracks").bounding_box()["y"]
    assert check(page, "transferir") == []
    shot(page, "transferir-para-revisar")
    page.click("#review-list [data-add-doubtful]")
    page.wait_for_selector("#review-list [data-add-doubtful].done")
    assert [c[0] for c in yt.calls].count("add") == 2                  # the transfer and the "?"
    page.click("#again")
    assert page.is_hidden("#review") and page.locator("#review-list li").count() == 0
    transfer(page, dry=True)
    assert page.is_hidden("#review")
    assert errors == []


def test_the_repeated_legend_only_shows_when_there_is_one(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    assert page.is_hidden("#n-rep")                      # nothing repeated: it takes no room in the legend
    assert errors == []


def test_coming_back_shows_the_current_playlists(browser, tmp_path):
    """A user liked more songs and the app kept the old list until disconnecting and connecting:
    only then was the stored one cleared. Coming back to the screen asks for them again, in Duplicates too."""
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("#src-list input[value=src]")
    name, current = tidal.playlists["src"]
    tidal.add_playlist("src", name, current + [S("Otra", "Alguien", id="nueva")])
    tidal.add_playlist("nueva", "Recién hecha", [S("A", "B", id="a"), S("C", "D", id="c")])
    page.click("nav [data-go=inicio]")
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("#src-list input[value=nueva]")
    assert page.get_attribute("#src-list input[value=src]", "data-count") == "5"
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=tidal]")
    page.wait_for_selector("#dup-playlist option[value=nueva]", state="attached")
    tidal.add_playlist("otra", "Más nueva", [S("E", "F", id="e"), S("G", "H", id="g")])
    page.click("nav [data-go=inicio]")
    page.click("nav [data-go=repetidos]")
    page.wait_for_selector("#dup-playlist option[value=otra]", state="attached")
    assert errors == []


def test_another_transfer_shows_the_current_source_too(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    tidal.add_playlist("nueva", "Recién hecha", [S("A", "B", id="a")])
    page.click("#again")
    page.wait_for_selector("#src-list input[value=nueva]")
    assert errors == []


def test_an_unknown_count_does_not_look_empty(browser, tmp_path):
    """Liked songs with 1600 songs showed no number, as if it had none."""
    from app.core.models import PlaylistInfo
    api, tidal, yt = make_api(tmp_path)
    tidal.list_playlists = lambda: [PlaylistInfo("src", "Favoritas", None)]
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("#src-list input[value=src]")
    assert page.inner_text("#src-list li .count") == "cantidad desconocida"
    assert errors == []


def test_the_remaining_time_is_shown_while_searching(browser, tmp_path):
    """1600 songs take about 26 minutes: without an estimate, it looks frozen."""
    from tests.ui_helpers import check
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    assert page.is_hidden("#eta")                        # it finished: nothing left to estimate
    eta = """([total, n, ms]) => { updateEta({ total, started: 0 }, n, ms); const el = document.querySelector('#eta'); return el.hidden ? null : el.textContent; }"""
    assert page.evaluate(eta, [100, 4, 40000]) is None                  # few songs: not yet
    assert page.evaluate(eta, [100, 10, 100000]) == "Faltan alrededor de 15 minutos de búsqueda"
    assert page.evaluate(eta, [100, 90, 90000]) == "Falta menos de un minuto de búsqueda"
    assert page.evaluate(eta, [100, 40, 40000]) == "Falta alrededor de 1 minuto de búsqueda"
    assert check(page, "transferir") == []
    shot(page, "transferir-tiempo-restante")
    assert page.evaluate(eta, [100, 100, 100000]) is None               # the last one
    assert errors == []


def test_a_redraw_keeps_what_was_chosen(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    yt.add_playlist("nueva", "Recién creada", [S("Uno", "X", id="n1")])
    page.evaluate("serviceChanged('ytmusic')")
    page.wait_for_selector("input[name=dst][value=nueva]")
    assert page.is_checked("input[name=src][value=src]") and page.is_checked("input[name=dst][value=dst]")
    assert page.is_enabled("#go") and errors == []


def test_the_history_shows_one_row_after_deciding_later(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    page.click("[data-alt='3:0']")
    page.wait_for_selector("text=Agregada la de Leonard Cohen")
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    assert page.locator("#hist li[data-op]").count() == 1
    page.click("#hist button.row")
    page.wait_for_selector("#hist li.open .detail .badge")
    sizes = page.evaluate("[...document.querySelectorAll('#hist .detail .badge')].map(b => b.getBoundingClientRect().width)")
    assert set(sizes) == {20}                                          # with long text it doesn't shrink either
    assert errors == []


def test_the_result_says_from_which_service_and_playlist_to_which(browser, tmp_path):
    """The user's request: make clear from where to where, service to service and playlist to playlist."""
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    assert page.inner_text("#run-route") == "Tidal · Favoritas a YouTube Music · Para correr"
    detail = page.inner_text("#done-detail")
    assert detail.startswith("Desde «Favoritas» de Tidal:\nA «Para correr» en YouTube Music: agregaste 1 tema")
    assert errors == []
