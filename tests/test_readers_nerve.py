"""Nerve and disability tables: the shapes MinerU actually returns."""

from core import readers

NERVE_HTML = (
    "<table><tr><td colspan='7'>Palpação dos nervos periféricos</td></tr>"
    "<tr><td rowspan='2'></td><td colspan='2'>Nervo cubital</td>"
    "<td colspan='2'>Radial cutâneo</td><td colspan='2'>Poplíteo externo</td></tr>"
    "<tr><td>D</td><td>E</td><td>D</td><td>E</td><td>D</td><td>E</td></tr>"
    "<tr><td>Grosso</td><td>—</td><td>+</td><td>—</td><td>+</td><td>—</td><td>+</td></tr>"
    "<tr><td>Dor</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>"
    "</table>"
)

NERVE_LATEX = (
    "$$\n+ / - \\begin{array}{c c c c c c} \\text {Nervocubital} & "
    "\\text {Radialcutáneo} & \\text {Popliteoexterno} \\\\ \\hline "
    "D & E & D & E & D & E \\\\ \\hline "
    "\\text {Grosso} & - & + & - & + & - & + \\\\ \\hline "
    "\\text {Dor} & - & - & - & - & - & - \\\\ \\hline \\end{array}\n$$"
)

SHORT_LATEX = (
    "$$\\begin{array}{c c c c c c} \\text {Nervocubital} & "
    "\\text {Radialcutáneo} & \\text {Popliteoexterno} \\\\ \\hline "
    "D & E & D & E & D & E \\\\ \\hline "
    "\\text {Grosso} & - & + & - & + & - \\\\ \\hline \\end{array}$$"
)


def test_html_with_a_title_row_maps_the_marked_side():
    values = readers._nerve(NERVE_HTML)
    positives = {key for key, value in values.items() if value == "+"}
    assert positives == {
        "nerve_enlargement_cubital_left",
        "nerve_enlargement_radial_cutaneous_left",
        "nerve_enlargement_popliteal_external_left",
    }
    assert len(values) == 12


def test_latex_array_reads_the_same_as_html():
    assert readers._nerve(NERVE_HTML) == readers._nerve(NERVE_LATEX)


def test_a_row_that_lost_a_cell_is_refused_not_guessed():
    # MinerU drops trailing cells in some LaTeX arrays. Reading anyway would
    # invent or lose a positive mark, so the reader returns nothing.
    assert readers._nerve(SHORT_LATEX) == {}


def test_muscle_letters_become_the_form_vocabulary():
    html = (
        "<table><tr><td>D</td><td colspan='2'>Testagem dos músculos</td><td>E</td></tr>"
        "<tr><td>N</td><td colspan='2'>Abdução dos dedos mínimos</td><td>F</td></tr>"
        "<tr><td>A</td><td colspan='2'>Dorsi-flexão dos pés</td><td>N</td></tr></table>"
    )
    values = readers._def_exam(html)
    assert values["right_hipothenar"] == "Normal"
    assert values["left_hipothenar"] == "Weak"
    assert values["right_foot_dorsiflex"] == "Paralysed"
    assert values["left_foot_dorsiflex"] == "Normal"
