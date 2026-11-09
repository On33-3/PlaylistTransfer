"""Transferir: elegir, en vivo, resultado y los "?" que se deciden al final."""
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
    tracks = page.inner_text("#run")                                # lista y "Para revisar"
    assert "No está la de Jeff Buckley" in tracks and "Ya estaba como «Mr. Blue Sky - 2012 Remaster»" in tracks
    shot(page, "4-transferir-resultado")
    page.click("[data-alt='3:0']")
    page.wait_for_selector("text=Agregada la de Leonard Cohen")
    assert [s.id for s in yt.songs("dst")] == ["y2old", "y1", "y3"]          # el en vivo (?) no se agregó solo
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    page.click("#hist button.row >> nth=1")
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
    """Sin casillas extra: ni reporte por transferencia ni "agregar los que pueden ser otra versión"."""
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=src]")
    assert page.locator("#pick input[type=checkbox]:not([name=dst]):not(#dst-new)").count() == 0
    transfer(page)
    page.wait_for_timeout(300)
    assert api._asked == []                                           # nunca se guarda un archivo solo
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
    """El caso de la captura: pocas playlists de origen y muchas de destino."""
    api, tidal, yt = make_api(tmp_path)
    for i in range(14):
        yt.add_playlist(f"extra{i}", f"Lista {i:02d}", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=dst][value=extra13]")
    heights = page.evaluate("[...document.querySelectorAll('.picker ul')].map(u => Math.round(u.getBoundingClientRect().height))")
    assert heights[0] == heights[1], heights
    tops = page.evaluate("[...document.querySelectorAll('.picker ul')].map(u => Math.round(u.getBoundingClientRect().top))")
    assert tops[0] == tops[1], f"las cajas no quedan alineadas: {tops}"   # buscador en las dos, aunque una sea corta
    first = page.locator("#dst-list li >> nth=0")
    assert "Crear una playlist nueva" in first.inner_text()
    page.fill(".picker:nth-child(2) .filter", "lista 1")
    visible = page.evaluate("[...document.querySelectorAll('#dst-list li[data-filter]')].filter(li => !li.hidden).map(li => li.querySelector('.name').textContent)")
    assert visible == ["Lista 10", "Lista 11", "Lista 12", "Lista 13"]          # "Lista 01" no contiene "lista 1"
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
    """En "Probar sin cambiar nada" no se agrega nada: la leyenda, las notas y los cuadrados lo dicen."""
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page, dry=True)
    legend = page.inner_text("#run .legend")
    assert "✓ Se agregaría" in legend and "? No se agregaría, puede ser otra versión" in legend
    assert "Agregado" not in legend
    assert "No se agregaría «Bohemian Rhapsody (Live Aid 1985)» de Queen" in page.inner_text("#run")
    assert "1 tema no se agregaría porque puede ser otra versión" in page.inner_text("#done")
    assert page.locator("#mosaic i >> nth=0").get_attribute("title").startswith("Take On Me — a-ha: Se agregaría.")
    page.click("#again")                                             # una transferencia real después
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
    assert sorted(s.id for s in yt.songs("dst")) == ["y1", "y2old"]          # solo Take On Me
    assert page.inner_text("#add-all") == "Agregar los 2 que pueden ser otra versión"
    shot(page, "13-agregar-igual")
    page.click("#run button[data-add-doubtful='1']")                 # uno solo
    page.wait_for_selector("#run button[data-add-doubtful='1'].done")
    assert page.inner_text("#run button[data-add-doubtful='1']") == "✓ Agregado"
    assert "y4" in [s.id for s in yt.songs("dst")]
    assert page.inner_text("#add-all") == "Agregar el que puede ser otra versión"
    page.click("#add-all")                                              # el resto, de una
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
    """De origen, solo las que tienen algo para pasar; de destino, cualquiera (una vacía sirve)."""
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
    """Lo pidió la primera usuaria: un tema repetido en la playlist de origen salía como "✓ Agregado"
    (y se agregaba una sola vez). Ahora tiene su cuadrado, su color y su número en la leyenda, en vivo
    y en el historial."""
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
    """Lo pidió la primera usuaria: los temas que esperan una decisión (un "?" para agregar igual, o la
    versión de otro artista) suben a "Para revisar", debajo del resultado y en el orden de la playlist;
    desde ahí se resuelven. En una prueba sin cambios no hay nada que decidir: no aparece."""
    from tests.ui_helpers import check
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    assert page.is_visible("#review")
    titles = page.eval_on_selector_all("#review-list .t", "ts => ts.map(t => t.textContent)")
    assert titles == ["Hallelujah", "Bohemian Rhapsody"]            # ✗ con otro artista y ?, en orden
    assert page.locator("#tracks [data-alt], #tracks [data-add-doubtful]").count() == 0
    review_top = page.locator("#review").bounding_box()["y"]
    assert page.locator("#done").bounding_box()["y"] < review_top < page.locator("#tracks").bounding_box()["y"]
    assert check(page, "transferir") == []
    shot(page, "transferir-para-revisar")
    page.click("#review-list [data-add-doubtful]")
    page.wait_for_selector("#review-list [data-add-doubtful].done")
    assert [c[0] for c in yt.calls].count("add") == 2                  # la transferencia y el "?"
    page.click("#again")
    assert page.is_hidden("#review") and page.locator("#review-list li").count() == 0
    transfer(page, dry=True)
    assert page.is_hidden("#review")
    assert errors == []


def test_the_repeated_legend_only_shows_when_there_is_one(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    assert page.is_hidden("#n-rep")                      # nada repetido: no ocupa lugar en la leyenda
    assert errors == []


def test_coming_back_shows_the_current_playlists(browser, tmp_path):
    """Un usuario sumó canciones a sus "Me gusta" y la app seguía con la lista vieja hasta desconectar y
    conectar: solo ahí se borraba lo guardado. Al volver a la pantalla se piden de nuevo, también en Repetidos."""
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
