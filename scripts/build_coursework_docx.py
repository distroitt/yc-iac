from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import html
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "coursework-explanatory-note.docx"

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU_PER_CM = 360000


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def attrs(**kwargs: str) -> str:
    return "".join(f' {key}="{esc(value)}"' for key, value in kwargs.items())


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Expected PNG image data")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def render_dot(source: str) -> bytes:
    completed = subprocess.run(
        ["dot", "-Tpng"],
        input=source.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout


def build_diagrams() -> dict[str, bytes]:
    common = 'graph [fontname="Times New Roman", bgcolor="white", pad="0.25", nodesep="0.55", ranksep="0.7"]; node [fontname="Times New Roman", shape=box, style="rounded,filled", color="#334155", fillcolor="#F8FAFC", margin="0.12,0.08"]; edge [fontname="Times New Roman", color="#475569"];'
    return {
        "components": render_dot(
            f"""
digraph G {{
  rankdir=LR;
  {common}
  cli [label="CLI\\nTyper commands", fillcolor="#DBEAFE"];
  manifest [label="Manifest Loader\\nYAML + Pydantic", fillcolor="#E0F2FE"];
  planner [label="Planner\\nplan: create/update/replace/delete", fillcolor="#DCFCE7"];
  commands [label="Plan Commands\\nCommand pattern", fillcolor="#FEF3C7"];
  executor [label="Executor\\nprogress + state save", fillcolor="#FFE4E6"];
  facade [label="YandexCloudFacade\\nSDK facade", fillcolor="#EDE9FE"];
  sdk [label="Yandex Cloud SDK\\ngRPC API", fillcolor="#F3E8FF"];
  state [label="StateStore\\nstate.json", fillcolor="#F1F5F9"];
  cli -> manifest -> planner -> commands -> executor -> facade -> sdk;
  executor -> state;
  planner -> state [label="read"];
}}
""",
        ),
        "apply": render_dot(
            f"""
digraph G {{
  rankdir=TB;
  {common}
  start [label="1. User runs\\niac-tool apply --confirm", fillcolor="#DBEAFE"];
  load [label="2. Load and validate manifest", fillcolor="#E0F2FE"];
  plan [label="3. Build execution plan\\nfrom manifest + state", fillcolor="#DCFCE7"];
  execute [label="4. Execute commands\\nin dependency order", fillcolor="#FEF3C7"];
  save [label="5. Save state after\\neach successful command", fillcolor="#F1F5F9"];
  outputs [label="6. Print live outputs", fillcolor="#EDE9FE"];
  start -> load -> plan -> execute -> save -> outputs;
  execute -> save [label="create/update/delete"];
}}
""",
        ),
        "resources": render_dot(
            f"""
digraph G {{
  rankdir=LR;
  {common}
  network [label="network", fillcolor="#DBEAFE"];
  sg [label="security_group\\nssh-access", fillcolor="#FCE7F3"];
  subnet [label="subnet", fillcolor="#DCFCE7"];
  disk [label="disk\\ndata-disk", fillcolor="#EDE9FE"];
  instance [label="instance\\nVM", fillcolor="#FEF3C7"];
  network -> sg;
  network -> subnet;
  subnet -> instance;
  sg -> instance;
  disk -> instance;
}}
""",
        ),
        "state": render_dot(
            f"""
digraph G {{
  rankdir=LR;
  {common}
  manifest [label="manifest.yaml\\ndesired configuration", fillcolor="#E0F2FE"];
  state [label="state.json\\nlogical_name -> resource_id\\nconfig_payload", fillcolor="#F1F5F9"];
  cloud [label="Yandex Cloud\\nreal resources", fillcolor="#EDE9FE"];
  plan [label="plan\\ncompare manifest with state", fillcolor="#DCFCE7"];
  drift [label="drift-detect / outputs\\nlive describe_*", fillcolor="#FEF3C7"];
  manifest -> plan;
  state -> plan;
  state -> cloud [label="resource_id"];
  cloud -> drift;
  manifest -> drift;
  state -> drift;
}}
""",
        ),
    }


@dataclass(frozen=True)
class Paragraph:
    text: str
    kind: str = "normal"


@dataclass(frozen=True)
class ImageAsset:
    rid: str
    name: str
    data: bytes
    width_emu: int
    height_emu: int


class DocxBuilder:
    def __init__(self) -> None:
        self.body: list[str] = []
        self.images: list[ImageAsset] = []

    def page_break(self) -> None:
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    def toc(self) -> None:
        self.body.append(
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-3" \\h \\z \\u </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>Для обновления содержания выделите это поле в Word или LibreOffice и выберите «Обновить поле».</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        )

    def paragraph(self, text: str = "", kind: str = "normal") -> None:
        if not text:
            self.body.append("<w:p/>")
            return
        if kind == "title":
            self.body.append(self._paragraph_xml(text, align="center", bold=True, size=28, spacing_after=0, first_line=0))
        elif kind == "subtitle":
            self.body.append(self._paragraph_xml(text, align="center", bold=False, size=28, spacing_after=0, first_line=0))
        elif kind == "center":
            self.body.append(self._paragraph_xml(text, align="center", bold=False, size=28, spacing_after=0, first_line=0))
        elif kind == "heading0":
            self.body.append(self._paragraph_xml(text.upper(), align="center", bold=True, size=28, spacing_before=240, spacing_after=240, first_line=0))
        elif kind == "heading1":
            self.body.append(self._paragraph_xml(text.upper(), align="left", bold=True, size=28, spacing_before=360, spacing_after=180, first_line=0, outline=0))
        elif kind == "heading2":
            self.body.append(self._paragraph_xml(text, align="left", bold=True, size=28, spacing_before=240, spacing_after=120, first_line=0, outline=1))
        elif kind == "caption":
            self.body.append(self._paragraph_xml(text, align="center", bold=False, size=28, spacing_before=120, spacing_after=120, first_line=0))
        elif kind == "list":
            self.body.append(self._paragraph_xml(text, align="both", bold=False, size=28, spacing_after=80, first_line=0, left=709))
        elif kind == "code":
            self.body.append(self._code_paragraph_xml(text))
        else:
            self.body.append(self._paragraph_xml(text, align="both", bold=False, size=28, spacing_after=80, first_line=709))

    def image(self, name: str, data: bytes, *, width_cm: float = 15.5) -> None:
        width_px, height_px = png_size(data)
        width_emu = int(width_cm * EMU_PER_CM)
        height_emu = int(width_emu * height_px / width_px)
        rid = f"rIdImage{len(self.images) + 1}"
        asset = ImageAsset(rid=rid, name=name, data=data, width_emu=width_emu, height_emu=height_emu)
        self.images.append(asset)
        doc_pr_id = len(self.images)
        self.body.append(
            '<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="80"/></w:pPr><w:r><w:drawing>'
            '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{asset.width_emu}" cy="{asset.height_emu}"/>'
            f'<wp:docPr id="{doc_pr_id}" name="{esc(name)}"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>'
            '</wp:cNvGraphicFramePr><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:nvPicPr>'
            f'<pic:cNvPr id="{doc_pr_id}" name="{esc(name)}"/>'
            '<pic:cNvPicPr/>'
            '</pic:nvPicPr>'
            '<pic:blipFill>'
            f'<a:blip r:embed="{asset.rid}" xmlns:r="{NS_R}"/>'
            '<a:stretch><a:fillRect/></a:stretch>'
            '</pic:blipFill>'
            '<pic:spPr>'
            f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{asset.width_emu}" cy="{asset.height_emu}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '</pic:spPr>'
            '</pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
        )

    def _paragraph_xml(
        self,
        text: str,
        *,
        align: str,
        bold: bool,
        size: int,
        spacing_before: int = 0,
        spacing_after: int = 80,
        first_line: int = 709,
        left: int = 0,
        outline: int | None = None,
    ) -> str:
        outline_xml = f'<w:outlineLvl w:val="{outline}"/>' if outline is not None else ""
        bold_xml = "<w:b/>" if bold else ""
        lines = text.split("\n")
        runs = []
        for index, line in enumerate(lines):
            if index:
                runs.append("<w:r><w:br/></w:r>")
            runs.append(
                '<w:r><w:rPr>'
                f'<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
                f"{bold_xml}<w:sz w:val=\"{size}\"/><w:szCs w:val=\"{size}\"/>"
                f'</w:rPr><w:t xml:space="preserve">{esc(line)}</w:t></w:r>'
            )
        return (
            "<w:p><w:pPr>"
            f'<w:jc w:val="{align}"/>'
            f'<w:spacing w:before="{spacing_before}" w:after="{spacing_after}" w:line="360" w:lineRule="auto"/>'
            f'<w:ind w:firstLine="{first_line}" w:left="{left}"/>'
            f"{outline_xml}"
            "</w:pPr>"
            + "".join(runs)
            + "</w:p>"
        )

    def _code_paragraph_xml(self, text: str) -> str:
        return (
            "<w:p><w:pPr>"
            '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>'
            '<w:ind w:firstLine="0" w:left="0"/>'
            "</w:pPr><w:r><w:rPr>"
            '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/>'
            '<w:sz w:val="18"/><w:szCs w:val="18"/>'
            f'</w:rPr><w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'
        )

    def document_xml(self) -> str:
        section = (
            "<w:sectPr>"
            '<w:footerReference w:type="default" r:id="rIdFooter1"/>'
            '<w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="567" w:bottom="1134" w:left="1701" w:header="708" w:footer="708" w:gutter="0"/>'
            '<w:cols w:space="708"/>'
            "</w:sectPr>"
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{NS_W}" xmlns:r="{NS_R}"><w:body>'
            + "".join(self.body)
            + section
            + "</w:body></w:document>"
        )


def split_paragraphs(text: str) -> list[str]:
    return [part.strip().replace("\n", " ") for part in text.strip().split("\n\n") if part.strip()]


def add_section(builder: DocxBuilder, title: str, text: str, *, numbered: bool = True) -> None:
    builder.paragraph(title, "heading1" if numbered else "heading0")
    for part in split_paragraphs(text):
        builder.paragraph(part)


def add_subsection(builder: DocxBuilder, title: str, text: str) -> None:
    builder.paragraph(title, "heading2")
    for part in split_paragraphs(text):
        builder.paragraph(part)


def add_inline_listing(builder: DocxBuilder, caption: str, code: str) -> None:
    builder.paragraph(caption, "caption")
    for line in code.strip("\n").splitlines():
        builder.paragraph(line, "code")


def title_pages(builder: DocxBuilder) -> None:
    for line in [
        "МИНИСТЕРСТВО ОБРАЗОВАНИЯ РЕСПУБЛИКИ БЕЛАРУСЬ",
        "УЧРЕЖДЕНИЕ ОБРАЗОВАНИЯ",
        "«БЕЛОРУССКИЙ ГОСУДАРСТВЕННЫЙ УНИВЕРСИТЕТ ИНФОРМАТИКИ И РАДИОЭЛЕКТРОНИКИ»",
    ]:
        builder.paragraph(line, "center")
    builder.paragraph("")
    builder.paragraph("Факультет: ________________________________", "center")
    builder.paragraph("Кафедра: ________________________________", "center")
    for _ in range(5):
        builder.paragraph("")
    builder.paragraph("ПОЯСНИТЕЛЬНАЯ ЗАПИСКА", "title")
    builder.paragraph("к курсовой работе", "subtitle")
    builder.paragraph("по дисциплине «Объектно-ориентированное программирование»", "subtitle")
    builder.paragraph("")
    builder.paragraph("на тему:", "subtitle")
    builder.paragraph("«Инструмент декларативного управления инфраструктурой (IaC) для облачного провайдера»", "title")
    builder.paragraph("")
    builder.paragraph("БГУИР КР 1-40 01 01 2026 ПЗ", "center")
    for _ in range(4):
        builder.paragraph("")
    builder.paragraph("Студент: ____________________ / ____________________", "normal")
    builder.paragraph("Группа: ____________________", "normal")
    builder.paragraph("Руководитель: ____________________ / ____________________", "normal")
    for _ in range(4):
        builder.paragraph("")
    builder.paragraph("Минск 2026", "center")
    builder.page_break()

    builder.paragraph("ЗАДАНИЕ НА КУРСОВУЮ РАБОТУ", "heading0")
    for item in [
        "Тема курсовой работы: «Инструмент декларативного управления инфраструктурой (IaC) для облачного провайдера».",
        "Исходные данные: язык программирования Python 3.12+, официальный Python SDK Yandex Cloud, локальный YAML-манифест инфраструктуры, локальный файл состояния, требования дисциплины «Объектно-ориентированное программирование».",
        "Перечень подлежащих разработке вопросов: анализ предметной области Infrastructure as Code; проектирование архитектуры CLI-инструмента; реализация загрузки и валидации манифеста; разработка планировщика изменений; интеграция с Yandex Cloud SDK; реализация локального состояния, команд apply/destroy, outputs, graph и drift-detect; тестирование.",
        "Перечень графического материала: диаграмма компонентов, диаграмма классов, диаграмма последовательности сценария apply, граф зависимостей ресурсов.",
        "Дата выдачи задания: «___» ____________ 2026 г. Срок сдачи: «___» ____________ 2026 г.",
    ]:
        builder.paragraph(item)
    builder.page_break()

    builder.paragraph("РЕФЕРАТ", "heading0")
    abstract = """
Пояснительная записка содержит описание разработки учебного программного средства для декларативного управления облачной инфраструктурой Yandex Cloud. Работа включает аналитический обзор подхода Infrastructure as Code, проектирование объектно-ориентированной архитектуры CLI-инструмента, описание реализации основных модулей, результаты тестирования и направления развития проекта.

Объектом разработки являются процессы описания, планирования и применения изменений облачной инфраструктуры. Предметом разработки являются методы декларативного управления ресурсами облачного провайдера с использованием языка Python и официального SDK. Цель работы — разработать прототип IaC-инструмента, который по YAML-манифесту строит план изменений, применяет его к Yandex Cloud, хранит локальное состояние и предоставляет средства диагностики.

В результате работы реализован CLI-инструмент, поддерживающий ресурсы network, security_group, subnet, disk и instance. Инструмент предоставляет команды validate, plan, apply, destroy, state, graph, drift-detect и outputs. Архитектура построена вокруг слоев CLI, загрузчика манифеста, планировщика, исполнителя команд, фасада облачного SDK и хранилища состояния. В проекте применены паттерны Facade, Factory и Command, а также полиморфизм обработчиков ресурсов.

Ключевые слова: INFRASTRUCTURE AS CODE, ОБЛАЧНАЯ ИНФРАСТРУКТУРА, PYTHON, YANDEX CLOUD, SDK, CLI, YAML, ОБЪЕКТНО-ОРИЕНТИРОВАННОЕ ПРОГРАММИРОВАНИЕ, ПЛАНИРОВЩИК, СОСТОЯНИЕ.
"""
    for part in split_paragraphs(abstract):
        builder.paragraph(part)
    builder.page_break()

    builder.paragraph("СОДЕРЖАНИЕ", "heading0")
    builder.toc()
    builder.page_break()

    builder.paragraph("ПЕРЕЧЕНЬ УСЛОВНЫХ ОБОЗНАЧЕНИЙ И СОКРАЩЕНИЙ", "heading0")
    for item in [
        "API — Application Programming Interface, программный интерфейс приложения.",
        "CLI — Command Line Interface, интерфейс командной строки.",
        "IaC — Infrastructure as Code, подход к управлению инфраструктурой как кодом.",
        "SDK — Software Development Kit, комплект средств разработки.",
        "VM — Virtual Machine, виртуальная машина.",
        "YAML — YAML Ain't Markup Language, человекочитаемый формат сериализации данных.",
        "State — локальное состояние, связывающее логические имена ресурсов с идентификаторами облачного провайдера.",
    ]:
        builder.paragraph(item, "list")
    builder.page_break()


INTRO = """
Современные программные системы редко ограничиваются только исходным кодом приложения. Для их работы требуются сети, подсети, виртуальные машины, диски, правила сетевого доступа, учетные записи и другие элементы инфраструктуры. При ручном создании таких ресурсов возрастает риск несогласованности окружений, появления ошибок в настройках безопасности и потери информации о том, какие именно изменения были внесены. Поэтому в промышленной разработке все чаще используется подход Infrastructure as Code, при котором инфраструктура описывается в виде декларативных файлов, хранится в системе контроля версий и применяется автоматизированными инструментами.

Актуальность темы обусловлена тем, что облачные провайдеры предоставляют богатые API, однако прямое ручное использование этих API не решает задачу воспроизводимости. Пользователю важно не только создать отдельный ресурс, но и получить управляемый жизненный цикл: проверить описание инфраструктуры, построить план изменений, понять последствия применения, выполнить операции в правильном порядке, сохранить связь между декларативным именем и реальным идентификатором ресурса, а затем при необходимости удалить инфраструктуру. Эти задачи требуют проектирования отдельного программного слоя, который соединяет удобный пользовательский интерфейс и API провайдера.

Цель курсовой работы — разработать объектно-ориентированный CLI-инструмент декларативного управления инфраструктурой Yandex Cloud на языке Python с использованием официального SDK. Инструмент должен читать YAML-манифест, валидировать его структуру, строить план изменений, применять инфраструктуру в облаке, хранить локальное состояние и предоставлять диагностические команды для просмотра состояния, графа зависимостей, live-outputs и расхождений между манифестом, state-файлом и реальным облаком.

Для достижения цели были поставлены следующие задачи: изучить предметную область Infrastructure as Code; сравнить декларативный и императивный подходы; определить функциональные и нефункциональные требования; спроектировать архитектуру приложения с выделением слоев ответственности; реализовать обработчики ресурсов network, security_group, subnet, disk и instance; разработать алгоритм планирования изменений; реализовать безопасное выполнение команд apply и destroy; добавить локальное состояние; провести unit- и интеграционное тестирование; подготовить демонстрационный сценарий для защиты.

Объектом разработки являются процессы управления облачной инфраструктурой. Предметом разработки являются методы декларативного описания, планирования и применения инфраструктурных изменений средствами объектно-ориентированного программирования. В рамках работы рассматривается не универсальная замена Terraform или Pulumi, а учебный прототип, позволяющий явно показать внутренние механизмы IaC-инструмента и связать их с паттернами проектирования.

Практическая значимость работы заключается в том, что созданный инструмент может использоваться как демонстрационный стенд для изучения IaC. Он показывает, как из декларативного YAML-файла формируется граф зависимостей ресурсов, как локальный state связывает логические имена с облачными идентификаторами, почему некоторые изменения можно применить как update, а некоторые требуют replace, и как диагностировать drift. Такой проект полезен не только как курсовая работа, но и как база для дальнейшего расширения: добавления новых ресурсов, импорта существующей инфраструктуры, удаленного состояния и refresh-логики.
"""


ANALYSIS = """
Infrastructure as Code представляет собой подход, при котором инфраструктура описывается и сопровождается так же дисциплинированно, как прикладной код. Вместо того чтобы вручную нажимать кнопки в консоли провайдера, разработчик описывает желаемое состояние в текстовом файле. Такой файл можно проверить, сохранить в репозитории, обсудить в code review, повторно применить в тестовом окружении и использовать как документированное описание системы. В отличие от разрозненных инструкций, декларативный манифест является одновременно спецификацией и входными данными для автоматизированного инструмента.

В императивном подходе пользователь описывает последовательность действий: создать сеть, затем создать подсеть, затем создать виртуальную машину, затем добавить правило доступа. Такой сценарий удобен для одноразовой автоматизации, но плохо отвечает на вопрос, что делать при повторном запуске. Если часть ресурсов уже существует, сценарий должен самостоятельно проверять состояние каждого элемента. Декларативный подход переносит акцент с последовательности действий на желаемый результат. Пользователь описывает, какие ресурсы должны существовать, а инструмент определяет, какие операции необходимо выполнить для достижения этого состояния.

Одним из ключевых понятий IaC является план изменений. План позволяет увидеть будущие действия до фактического применения. В учебном инструменте план представлен набором изменений create, update, replace, delete и noop. Create означает, что ресурс отсутствует в локальном состоянии и должен быть создан. Update означает, что изменились поля, которые провайдер позволяет обновить без пересоздания ресурса. Replace используется для изменений, которые технически или логически требуют удаления старого ресурса и создания нового. Delete применяется к ресурсам, которые остались в state-файле, но отсутствуют в текущем манифесте. Noop означает отсутствие изменений.

Существующие инструменты, такие как Terraform, решают задачу IaC на промышленном уровне. Terraform использует декларативный язык HCL, хранит state, строит план изменений и поддерживает большое число провайдеров. Его сильной стороной является зрелая экосистема и богатый набор ресурсов. Однако внутренняя логика Terraform скрыта от пользователя, а реализация собственного провайдера требует изучения отдельного SDK. Для курсовой работы важно не только получить результат, но и показать устройство планировщика, исполнителя, state-хранилища и фасада облачного API, поэтому разработка собственного инструмента имеет учебную ценность.

Pulumi предлагает другой подход: инфраструктура описывается на обычных языках программирования. Это удобно для команд, которые хотят использовать TypeScript, Python, Go или C# и применять привычные средства разработки. При этом Pulumi сохраняет идею state и планирования. В контексте данной работы Pulumi интересен как пример сочетания IaC и полноценного языка программирования, однако цель проекта состоит в построении собственного CLI-инструмента с явной объектно-ориентированной архитектурой, а не в использовании готовой платформы.

Прямое использование CLI облачного провайдера или SDK также возможно. Например, пользователь может написать набор команд для создания сети, подсети и виртуальной машины. Такой подход дает полный контроль над API, но не предоставляет декларативного плана и локального состояния из коробки. Именно поэтому в работе выбран промежуточный вариант: официальный SDK используется как низкоуровневый механизм взаимодействия с Yandex Cloud, а логика IaC реализуется в собственном приложении. Это позволяет продемонстрировать проектирование слоев и паттернов без необходимости разрабатывать сетевой протокол вручную.

Yandex Cloud выбран в качестве целевого провайдера, поскольку он предоставляет официальный Python SDK, gRPC API и типовые ресурсы, достаточные для демонстрации инфраструктурного сценария. В минимальный набор вошли сеть, группа безопасности, подсеть, диск и виртуальная машина. Эти ресурсы образуют естественный граф зависимостей: подсеть зависит от сети, группа безопасности зависит от сети, виртуальная машина зависит от подсети, групп безопасности и дополнительных дисков. Такой граф позволяет показать топологическую сортировку и обратный порядок удаления.

Язык Python выбран из-за читаемости, развитой экосистемы и удобства быстрого прототипирования. Для CLI используется Typer, так как он позволяет декларативно описывать команды и параметры на основе type hints. Для загрузки YAML применяется PyYAML, а для строгой валидации схемы — Pydantic. Такой стек соответствует учебной задаче: он достаточно прост для понимания, но одновременно демонстрирует современные практики разработки Python-приложений.

С точки зрения объектно-ориентированного программирования проект интересен тем, что каждый тип ресурса представлен отдельным обработчиком, но все обработчики имеют общий базовый контракт. Это позволяет планировщику работать с абстракцией CloudResourceHandler и не знать деталей создания сети или виртуальной машины. Конкретные действия инкапсулируются в подклассах, а общий жизненный цикл описывается через полиморфизм. Такой подход хорошо демонстрирует принцип открытости-закрытости: для добавления нового ресурса нужно создать новый обработчик, не переписывая весь планировщик.
"""


REQUIREMENTS = """
Функциональные требования к системе сформированы исходя из типового жизненного цикла IaC-инструмента. Пользователь должен иметь возможность проверить манифест до обращения к облаку, построить план изменений, применить инфраструктуру, удалить ее, просмотреть локальное состояние, получить граф зависимостей, диагностировать drift и вывести полезные live-outputs. Каждая команда должна иметь понятный CLI-интерфейс и завершаться предсказуемым кодом возврата, чтобы ее можно было использовать в демонстрационных сценариях и автоматизации.

Команда validate предназначена для раннего обнаружения ошибок. Она проверяет синтаксис YAML, обязательные поля, типы значений, корректность CIDR-блоков, существование SSH public key-файла и ссылки между логическими ресурсами. Важно, что такие ошибки обнаруживаются до обращения к Yandex Cloud. Это снижает стоимость ошибок и делает поведение инструмента более дружелюбным для пользователя.

Команда plan должна строить список изменений на основе текущего манифеста и локального state-файла. В рамках MVP план не выполняет live-refresh облака, поэтому он сравнивает желаемую конфигурацию с последней успешно примененной конфигурацией. Такое решение проще и предсказуемее для учебного проекта. Для проверки фактического состояния облака добавлена отдельная команда drift-detect, которая уже обращается к Yandex Cloud через describe-методы фасада.

Команда apply выполняет команды плана. Для безопасности она требует явного флага --confirm. Это предотвращает случайное создание или удаление платных ресурсов. Во время выполнения выводится прогресс по каждой команде, а после успешного применения печатаются стандартные outputs. Если операция частично завершилась, state сохраняется после каждого успешного шага, поэтому пользователь может повторить apply или выполнить destroy для очистки уже созданных ресурсов.

Команда destroy удаляет управляемую инфраструктуру в обратном порядке зависимостей. Это особенно важно для ресурсов, связанных отношениями использования. Например, диск нельзя удалить, пока он подключен к виртуальной машине, а подсеть нельзя удалить, пока в ней находятся сетевые интерфейсы. Планировщик сортирует ресурсы топологически, а destroy использует обратный порядок, чтобы минимизировать ошибки удаления.

Команда state показывает локальное состояние. State-файл хранит logical_name, resource_type, resource_id, зависимости, hash и payload последней примененной конфигурации. Logical name — это имя ресурса внутри манифеста, стабильное для пользователя. Resource ID — это идентификатор, выданный Yandex Cloud. Разделение этих понятий принципиально важно: пользователь может изменить отображаемое имя ресурса в облаке, но логическое имя должно оставаться ключом управления.

Команда graph генерирует граф зависимостей в формате Graphviz DOT. Эта команда не обращается к облаку и работает только по манифесту. Она полезна для защиты курсовой работы, потому что позволяет визуально показать порядок создания ресурсов и связи между ними. Генерируемый граф также помогает обнаружить ошибочные зависимости и объяснить, почему определенные ресурсы создаются раньше других.

Команда drift-detect сравнивает манифест, state и фактическое состояние облака. Она показывает, какие ресурсы синхронизированы, какие отсутствуют в state, какие исчезли из облака и какие имеют расхождения по наблюдаемым полям. Drift-detect не исправляет расхождения автоматически, а выполняет диагностическую роль. Это соответствует принципу осторожности: автоматическое исправление drift без явного plan/apply может быть неожиданным для пользователя.

Команда outputs собирает стандартные значения из live-состояния облака. Для instance выводятся status, fqdn, internal_ip, public_ip, subnet_id и security_group_ids. Для subnet выводятся CIDR-блоки, zone_id и network_id. Для disk выводятся size_gb, type_id и attached_instance_ids. Outputs полезны после apply, когда пользователю нужно быстро получить IP-адрес виртуальной машины для SSH-подключения.

Нефункциональные требования включают расширяемость, читаемость, безопасность хранения учетных данных, устойчивость к частичным сбоям и тестируемость. Расширяемость обеспечивается через абстрактный базовый класс обработчика ресурса и фабрику. Читаемость достигается разделением модулей по ответственности. Секреты не хранятся в манифесте: IAM token, OAuth token или service account key передаются через переменные окружения или локальный конфигурационный файл, который не должен коммититься.

Требование устойчивости к частичным сбоям особенно важно при работе с реальным облаком. Операции создания и удаления могут завершаться ошибками из-за квот, лимитов, неверных параметров или временных проблем провайдера. Поэтому состояние сохраняется после каждого успешного действия, а не только в конце apply. Если создание сети и подсети прошло успешно, а создание VM завершилось ошибкой, state будет содержать уже созданные ресурсы, и destroy сможет корректно их удалить.
"""


DESIGN = """
Архитектура проекта построена по цепочке CLI — Manifest Loader — Planner — Executor — Yandex Cloud Facade — State Store. Каждый слой имеет ограниченную ответственность. CLI отвечает за пользовательский интерфейс и обработку параметров. Manifest Loader загружает YAML и строит Pydantic-модель. Planner анализирует желаемое состояние и локальный state. Executor выполняет команды плана. Facade скрывает детали Yandex Cloud SDK. State Store отвечает за чтение и атомарную запись state.json.

Такое разделение упрощает тестирование. Планировщик можно тестировать без облака, передавая ему искусственное состояние. Executor можно тестировать с fake-фасадом, который только записывает вызовы. Manifest Loader тестируется через временные YAML-файлы. Facade содержит интеграцию с реальным SDK и поэтому отделен от бизнес-логики. Благодаря этому большая часть тестов является unit-тестами и выполняется быстро.

Манифест имеет раздел provider и коллекции resources: networks, security_groups, subnets, disks и instances. Provider хранит folder_id, zone_id и project_name. Ресурсы описывают только декларативные параметры. Например, subnet содержит logical_name, name, network, cidr и labels. Instance содержит параметры платформы, ресурсов, boot disk, образа, пользователя, SSH-ключа, public IP, security groups и data disks. Связи задаются через logical names, а не через реальные cloud IDs.

Использование logical names позволяет описывать инфраструктуру независимо от конкретного облачного состояния. Пользователь пишет, что instance зависит от subnet с logical_name "subnet", а инструмент сам находит в state реальный resource_id этой подсети. Такой подход похож на адресацию ресурсов в Terraform, но реализован в упрощенном виде. Он делает манифест переносимым между окружениями, если state-файл создается заново.

State-файл выполняет несколько функций. Во-первых, он хранит соответствие между logical name и resource ID. Без него инструмент не знал бы, какой именно ресурс в облаке нужно обновлять или удалять. Во-вторых, он хранит зависимости, чтобы destroy мог удалить ресурсы в правильном порядке даже если манифест изменился. В-третьих, он хранит config_hash и config_payload последней примененной конфигурации, что позволяет отличить updateable-изменения от replace-only изменений.

Поле config_payload является важным проектным решением. Если хранить только hash, планировщик видит лишь факт изменения, но не знает, изменилось ли безопасное поле name или критичное поле image_family. Payload хранит нормализованный снимок примененной декларативной конфигурации. Это не является секретным состоянием облака и не заменяет live-refresh, но дает планировщику достаточно информации для принятия решения update или replace.

Планировщик сначала строит список обработчиков ресурсов через ResourceHandlerFactory. Затем он проверяет граф зависимостей: каждая ссылка должна указывать на существующий logical name, циклы недопустимы. После этого обработчики сортируются топологически. Топологическая сортировка гарантирует, что при создании ресурс будет обработан после своих зависимостей. Например, subnet создается после network, а instance — после subnet, security_group и disk.

Для apply-плана каждый ресурс сравнивается с state. Если ресурса нет, выбирается create. Если тип изменился, выбирается replace. Если hash изменился, планировщик вызывает can_update у обработчика ресурса. Если обработчик подтверждает возможность обновления на месте, выбирается update; иначе replace. После первичного прохода планировщик выполняет распространение изменений по зависимостям: если зависимость будет пересоздана, зависимый ресурс тоже может потребовать replace.

Команды плана реализуют паттерн Command. Каждая операция представлена объектом с методом execute. CreateResourceCommand вызывает handler.create, сохраняет ресурс в state и записывает state на диск. UpdateResourceCommand вызывает handler.update и обновляет запись state. DeleteResourceCommand вызывает handler.delete и удаляет запись. DeleteStateResourceCommand нужен для orphaned-ресурсов, когда ресурс есть в state, но отсутствует в текущем манифесте.

Фасад YandexCloudFacade реализует паттерн Facade. Он скрывает от остальных модулей детали импортов protobuf-классов, создания gRPC-запросов, ожидания операций и обработки ошибок. Остальная система не знает, как именно устроен CreateInstanceRequest или UpdateDiskRequest. Она вызывает методы create_instance, delete_instance, update_instance_security_groups и другие высокоуровневые методы.

Фабрика ResourceHandlerFactory реализует паттерн Factory. Она принимает объект Manifest и создает список обработчиков ресурсов. Это отделяет процесс разбора манифеста от процесса планирования. Планировщик получает уже готовые объекты с единым интерфейсом. Если в будущем потребуется добавить новый тип ресурса, например bucket или load balancer, достаточно добавить новую Pydantic-модель, обработчик и регистрацию в фабрике.

Обработчики ресурсов демонстрируют полиморфизм. NetworkResourceHandler, SubnetResourceHandler, DiskResourceHandler, SecurityGroupResourceHandler и InstanceResourceHandler наследуются от CloudResourceHandler. Каждый обработчик реализует fingerprint_payload, create, delete и при необходимости update. Планировщик и executor работают через базовый тип, не завися от конкретного класса. Это снижает связанность и делает архитектуру гибкой.

Политика update/replace в проекте выбрана консервативной. Поля name и labels обычно можно обновлять без пересоздания. Размер disk можно увеличить через update, но изменение типа диска требует replace. У instance можно обновлять security_groups, name, labels, preemptible, cores и memory_gb, причем изменение cores или memory_gb требует временной остановки VM. Изменение image_family, username, boot_disk_gb, subnet или data_disks считается replace, так как оно затрагивает базовую конфигурацию VM.

Для subnet изменение CIDR отнесено к replace-only. Хотя SDK содержит UpdateSubnetRequest с полем v4_cidr_blocks, практическое тестирование показало, что облако не позволяет изменить CIDR, если в старом диапазоне уже есть выделенные адреса. Остановка VM не освобождает внутренние IP-адреса сетевых интерфейсов. Поэтому безопаснее пересоздавать subnet и зависимые instance, чем пытаться выполнить update, который упадет в середине apply.
"""


IMPLEMENTATION = """
Проект реализован как Python-пакет с исходным кодом в каталоге src/iac_tool. Точка входа задается в pyproject.toml через console script iac-tool. Такой способ установки удобен: после команды pip install -e ".[dev]" пользователь получает CLI-команду iac-tool в виртуальном окружении. В зависимостях указаны Typer, PyYAML, Pydantic и yandexcloud, а для разработки используется pytest.

Модуль manifest.py отвечает за загрузку YAML и построение Pydantic-модели. Он поддерживает как единственные секции network, subnet, instance, так и коллекции networks, subnets, instances. Это было важно для постепенного развития проекта: сначала MVP работал с одним ресурсом каждого типа, затем появилась поддержка нескольких ресурсов. Нормализация входного YAML позволяет сохранить обратную совместимость.

Pydantic-валидация используется не только для типов, но и для предметных правил. Имена ресурсов проверяются на допустимость, CIDR-блоки валидируются через модуль ipaddress, SSH public key path разворачивается относительно файла манифеста. Модель Manifest проверяет уникальность logical_name и корректность ссылок между ресурсами. Благодаря этому многие ошибки обнаруживаются до обращения к облаку.

Модуль resources.py содержит объектную модель обработчиков ресурсов. Базовый класс CloudResourceHandler определяет свойства resource_type, logical_name, dependencies и методы fingerprint_payload, create, delete, update. Метод build_state создает ResourceState на основе resource_id и текущего fingerprint. Метод can_update сравнивает старый config_payload с новым и проверяет, что изменились только поля из updatable_fields.

Fingerprint payload строится как словарь стабильных значений, влияющих на конфигурацию ресурса. Для instance в fingerprint входит содержимое публичного SSH-ключа, а не только путь к файлу. Это важно: если пользователь заменит содержимое ключа в том же файле, hash изменится, и планировщик увидит необходимость пересоздания VM. Для labels, правил security group и списков зависимостей используется JSON-сериализация с сортировкой ключей.

Модуль planner.py строит apply- и destroy-планы. Apply-план включает изменения по ресурсам из текущего манифеста и orphaned-ресурсы из state. Destroy-план строится по всем ресурсам state, потому что даже если манифест изменился, destroy должен очистить управляемую инфраструктуру. Для сортировки state-ресурсов используется отдельная функция, которая учитывает сохраненные зависимости.

Модуль commands.py содержит классы команд. Важная особенность состоит в том, что команда сохраняет state сразу после успешного действия. Это делает выполнение более устойчивым. Например, если apply состоит из пяти команд и четвертая завершается ошибкой, первые три изменения уже записаны. Пользователь видит ошибку, но state отражает реальность лучше, чем если бы запись выполнялась только в конце.

Модуль executor.py выполняет команды последовательно и предоставляет progress_callback. Этот callback используется CLI для вывода строк вида [1/4] create network:network ... Даже без verbose пользователь видит, что операция действительно идет. При ошибке executor логирует traceback, отправляет событие failed и выбрасывает ExecutionError с контекстом команды. Это облегчает диагностику.

Модуль facade.py интегрируется с Yandex Cloud SDK. Фасад лениво создает SDK-клиент на основе настроек аутентификации. Поддерживаются IAM token, OAuth token и service account key file. Для каждого ресурса реализованы методы create, delete, describe и частично update. Операции Yandex Cloud являются асинхронными, поэтому фасад получает operation_id и опрашивает OperationService до завершения или тайм-аута.

Обработка ошибок в фасаде разделяет provider errors и not found. Если delete получает NOT_FOUND, операция считается успешной: ресурс уже отсутствует в облаке, значит желаемое состояние удаления достигнуто. Для describe NOT_FOUND преобразуется в ResourceNotFoundError, чтобы drift-detect и outputs могли помечать ресурс как missing_in_cloud, а не завершать работу всего отчета.

Модуль state.py реализует InfrastructureState и StateStore. StateStore читает JSON, валидирует его через Pydantic и записывает файл атомарно: сначала создается временный файл .state.json.tmp, затем выполняется replace. Такой подход снижает риск повреждения state при прерывании процесса записи. State-файл по умолчанию создается рядом с манифестом, но путь можно переопределить через --state-file.

Модуль drift.py реализует сравнение desired и observed payload. Desired payload строится из манифеста и state, потому что некоторые поля, например subnet_id или security_group_ids, известны только после разрешения зависимостей через resource_id. Observed payload получается через describe_* методы фасада. Если ресурс отсутствует в state, он помечается missing_in_state. Если resource_id отсутствует в облаке, он помечается missing_in_cloud.

Модуль outputs.py использует похожий read-only подход, но цель другая: не найти расхождения, а вывести полезные значения. Для каждой записи формируется ResourceOutputs со статусом available, missing_in_state или missing_in_cloud. Команда outputs не должна падать из-за drift одного ресурса: она показывает частичные данные и предупреждения. Это повышает удобство после apply, когда пользователь хочет быстро получить public_ip.

Модуль graphing.py строит DOT-представление графа зависимостей. Узлы получают визуальное оформление в зависимости от типа ресурса. Ребра направлены от зависимости к зависимому ресурсу. Генерируемый DOT можно передать утилите dot для получения PNG. Эта функция полезна для документации и защиты, потому что визуально показывает, почему порядок операций не произволен.

Модуль cli.py объединяет все слои. Typer-команды загружают манифест, создают StateStore, строят Planner и вызывают нужный сценарий. Общая обработка ошибок выводит цепочку причин и подсказку. Для известных ситуаций, например RESOURCE_EXHAUSTED по externalAddressesCreation.rate, CLI выводит более конкретную рекомендацию: подождать, повторить apply или отключить public IP для части VM.

Важной частью реализации стала поддержка безопасных updates. Первоначально MVP предполагал create, delete и replace. Затем была добавлена возможность обновлять некоторые поля без пересоздания. Это потребовало расширить state полем config_payload. На практике выяснилось, что не все поля, формально присутствующие в UpdateRequest SDK, можно безопасно менять в работающей инфраструктуре. Поэтому политика update/replace была уточнена на основе реальных ошибок Yandex Cloud.
"""


TESTING = """
Тестирование проекта построено преимущественно на unit-тестах. Это соответствует архитектуре: большинство модулей можно проверить без доступа к реальному облаку. Тесты manifest проверяют загрузку YAML, поддержку множественных ресурсов, валидацию ссылок, проверку SSH public key path и ошибки схемы. Такие тесты защищают границу пользовательского ввода.

Тесты planner проверяют создание плана из пустого state, noop при совпадении конфигурации, удаление orphaned-ресурсов, замену при изменении replace-only полей, update при изменении безопасных полей и каскадное влияние зависимостей. Отдельно проверяется сценарий изменения security groups у instance, когда новая security group создается, а instance обновляется без пересоздания.

Тесты executor используют FakeFacade. Это позволяет проверить порядок вызовов без реального Yandex Cloud. Например, apply должен вызвать create_network, create_subnet и create_instance в прямом порядке, а destroy — delete_instance, delete_subnet и delete_network в обратном. Также проверяется обновление state после update и сохранение зависимостей.

Тесты facade_sdk_shapes проверяют совместимость с protobuf-классами SDK. Они создают CreateInstanceRequest, UpdateInstanceNetworkInterfaceRequest, UpdateDiskRequest и другие запросы, чтобы убедиться, что используемые поля действительно присутствуют в установленной версии SDK. Это особенно важно, потому что SDK генерируется из protobuf-схем, и неправильное имя поля проявится только во время выполнения.

Тесты drift и outputs используют fake-данные observed payload. Они проверяют статусы in_sync, drifted, missing_in_state, missing_in_cloud и orphaned_in_state. Для outputs проверяется извлечение public_ip, internal_ip, status и других значений. Это подтверждает, что read-only команды корректно обрабатывают частичную недоступность ресурсов.

Тесты observability проверяют форматирование цепочки исключений и работу логирования. Это важно для пользовательского опыта. В реальных облачных сценариях ошибки часто приходят как вложенные gRPC-исключения, и без аккуратного форматирования пользователь видит длинный traceback без понятной причины. Функция format_exception_chain оставляет полезный контекст и убирает дубли.

Интеграционный тест с реальным облаком помечен маркером integration и запускается только при наличии переменной YC_RUN_INTEGRATION=1. Для него требуются folder_id, zone_id и путь к SSH public key. Такой тест создает инфраструктуру в тестовой папке Yandex Cloud, проверяет применение и затем удаляет ресурсы. По умолчанию он пропускается, чтобы обычный pytest не создавал платные ресурсы.

Ручной демонстрационный сценарий включает последовательность validate, plan, apply, outputs, state, drift-detect, graph и destroy. На защите можно показать исходный YAML, выполнить validate для подтверждения корректности, затем plan для демонстрации будущих действий. После apply можно открыть Yandex Cloud Console и убедиться, что ресурсы созданы. Затем outputs показывает public_ip, а destroy очищает инфраструктуру.

В ходе тестирования были выявлены важные ограничения. Во-первых, изменение CIDR подсети нельзя считать безопасным update, если в старом диапазоне есть выделенные адреса. Остановка VM не освобождает внутренние IP сетевых интерфейсов. Поэтому изменение CIDR классифицируется как replace для subnet и зависимых instance. Во-вторых, изменение cores и memory_gb у instance требует остановки VM, что было учтено в фасаде и обработчике instance.

Текущий набор автоматических тестов покрывает основные сценарии MVP. Последний локальный прогон включал unit-тесты CLI, manifest, planner, executor, state, drift, outputs, observability и SDK-shape проверки. Интеграционный тест оставлен опциональным из-за необходимости реального аккаунта и возможных затрат. Такой подход обеспечивает баланс между надежностью и безопасностью разработки.
"""


CONCLUSION = """
В результате курсовой работы разработан учебный CLI-инструмент декларативного управления инфраструктурой Yandex Cloud. Инструмент позволяет описывать инфраструктуру в YAML-манифесте, валидировать описание, строить план изменений, применять инфраструктуру, удалять ее, просматривать state, генерировать граф зависимостей, диагностировать drift и получать live-outputs. Реализация подтверждает, что основные идеи IaC можно продемонстрировать в относительно компактном, но архитектурно осмысленном Python-проекте.

Цель работы достигнута. Были изучены принципы Infrastructure as Code и особенности декларативного подхода. Были сформулированы требования к системе, спроектирована многослойная архитектура, реализованы обработчики ресурсов, планировщик, исполнитель команд, фасад Yandex Cloud SDK и локальное state-хранилище. Проект использует объектно-ориентированные принципы: абстракцию, наследование, полиморфизм и инкапсуляцию. Также применены паттерны Facade, Factory и Command.

Практическая ценность проекта заключается в том, что он показывает внутреннее устройство IaC-инструмента. Пользователь видит не только результат создания ресурсов, но и механизм: как строится граф зависимостей, как state связывает logical names с resource IDs, как определяется update или replace, как обрабатываются ошибки облачного провайдера и почему destroy должен идти в обратном порядке. Это делает проект полезным для обучения и демонстрации на защите.

Ограничения проекта связаны с его учебным характером. Поддерживается ограниченный набор ресурсов, state хранится локально, нет блокировки state для параллельной работы, отсутствует полноценный refresh перед plan, а политика update реализована только для наиболее понятных полей. Однако эти ограничения осознанны и зафиксированы. Они оставляют пространство для дальнейшего развития.

Направления развития включают добавление команды refresh и режима plan --refresh, импорт существующих ресурсов в state, поддержку удаленного state-хранилища, блокировку state, workspace-окружения, дополнительные ресурсы Yandex Cloud, более гибкую модель outputs, улучшенную миграцию ресурсов и расширенную интеграционную тестовую среду. Также возможно создание web-интерфейса поверх существующих слоев, поскольку бизнес-логика уже отделена от CLI.
"""


SOURCES = [
    "СТП 01–2024. Дипломные проекты (работы). Общие требования. Стандарт предприятия. Минск: БГУИР, 2024.",
    "Yandex Cloud. SDK quickstart. URL: https://yandex.cloud/en/docs/overview/sdk/quickstart.",
    "Yandex Cloud. Python SDK repository. URL: https://github.com/yandex-cloud/python-sdk.",
    "Yandex Cloud. Compute InstanceService gRPC reference. URL: https://yandex.cloud/ru/docs/compute/api-ref/grpc/Instance/create.",
    "Yandex Cloud. VPC gRPC API reference. URL: https://yandex.cloud/en/docs/vpc/api-ref/grpc/.",
    "HashiCorp Developer. Terraform State. URL: https://developer.hashicorp.com/terraform/language/state.",
    "Typer documentation. URL: https://typer.tiangolo.com/.",
    "Pydantic documentation. URL: https://docs.pydantic.dev/.",
    "pytest documentation. URL: https://docs.pytest.org/.",
    "Graphviz DOT language documentation. URL: https://graphviz.org/doc/info/lang.html.",
]


def add_main_text(builder: DocxBuilder, diagrams: dict[str, bytes]) -> None:
    add_section(builder, "Введение", INTRO, numbered=False)
    add_section(builder, "1 Аналитический обзор предметной области", ANALYSIS)
    add_subsection(
        builder,
        "1.1 Выбор технологического стека",
        """
Выбор технологического стека определялся требованиями учебного проекта. Необходимо было получить достаточно выразительный язык, удобные средства валидации, простой CLI и официальный доступ к API облачного провайдера. Python удовлетворяет этим требованиям благодаря лаконичному синтаксису, развитой экосистеме и поддержке type hints. Наличие Pydantic позволило описать схему манифеста как набор классов, а Typer упростил создание команд validate, plan, apply и других.

Использование официального SDK Yandex Cloud снижает риск несовместимости с API провайдера. Вместо ручной сборки HTTP-запросов приложение использует сгенерированные protobuf-классы и gRPC-stub. Это также удобно для тестирования формы запросов: можно создать объект UpdateInstanceRequest в unit-тесте и убедиться, что используемые поля существуют в установленной версии SDK.
""",
    )
    add_section(builder, "2 Требования к программному средству", REQUIREMENTS)
    add_section(builder, "3 Проектирование программного средства", DESIGN)
    builder.image("component-architecture.png", diagrams["components"])
    builder.paragraph("Рисунок 3.1 — Компонентная архитектура IaC-инструмента", "caption")
    add_subsection(
        builder,
        "3.1 Диаграммы и графические материалы",
        """
Для пояснения архитектуры подготовлены PlantUML-диаграммы. Диаграмма компонентов показывает взаимодействие CLI, загрузчика манифеста, планировщика, исполнителя, фасада SDK и хранилища состояния. Диаграмма классов отражает наследование обработчиков ресурсов от CloudResourceHandler и использование команд плана. Диаграмма последовательности apply показывает порядок действий от запуска команды до сохранения state после каждой операции.

Отдельным графическим материалом является граф зависимостей ресурсов. Он строится автоматически командой graph и может быть визуализирован средствами Graphviz. В отличие от статической диаграммы компонентов, этот граф зависит от конкретного манифеста. Поэтому он полезен как пользовательская функция и как материал для защиты: можно показать, что порядок создания и удаления вычисляется не вручную, а на основе зависимостей.
""",
    )
    builder.image("resource-dependency-graph.png", diagrams["resources"], width_cm=13.5)
    builder.paragraph("Рисунок 3.2 — Граф зависимостей ресурсов демонстрационного манифеста", "caption")
    builder.image("state-manifest-cloud.png", diagrams["state"])
    builder.paragraph("Рисунок 3.3 — Связь манифеста, локального состояния и реального облака", "caption")
    builder.paragraph(
        "Структура локального состояния является центральным элементом проектирования. В листинге 3.1 показано, что state хранит не только идентификатор ресурса в облаке, но и зависимости, hash и payload последней примененной конфигурации. Благодаря этому планировщик может принимать решение о типе изменения без обращения к облаку.",
    )
    add_inline_listing(
        builder,
        "Листинг 3.1 — Модель состояния управляемого ресурса",
        """
class ResourceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_name: str
    resource_type: str
    resource_id: str
    config_hash: str
    config_payload: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
""",
    )
    builder.paragraph(
        "Решение о возможности обновления на месте вынесено в базовый обработчик ресурса. Листинг 3.2 показывает общий механизм: сравниваются старый и новый payload, после чего проверяется, входят ли измененные поля в множество updatable_fields конкретного ресурса.",
    )
    add_inline_listing(
        builder,
        "Листинг 3.2 — Общая проверка возможности update",
        """
def changed_fields(self, resource: ResourceState) -> set[str]:
    old_payload = resource.config_payload
    if not old_payload:
        return set()
    new_payload = self.fingerprint_payload()
    return {
        key
        for key in old_payload.keys() | new_payload.keys()
        if old_payload.get(key) != new_payload.get(key)
    }

def can_update(self, resource: ResourceState) -> bool:
    if resource.resource_type != self.resource_type:
        return False
    changed = self.changed_fields(resource)
    return bool(changed) and changed <= self.updatable_fields
""",
    )
    add_section(builder, "4 Реализация программного средства", IMPLEMENTATION)
    builder.image("apply-sequence.png", diagrams["apply"], width_cm=13.5)
    builder.paragraph("Рисунок 4.1 — Последовательность выполнения команды apply", "caption")
    builder.paragraph(
        "Ключевой фрагмент планировщика приведен в листинге 4.1. Он показывает, как на основе state и текущего обработчика выбирается create, update, replace или noop. Этот код является ядром декларативного поведения инструмента.",
    )
    add_inline_listing(
        builder,
        "Листинг 4.1 — Выбор типа изменения в планировщике",
        """
if current is None:
    decisions[handler.logical_name] = ChangeKind.CREATE
elif current.resource_type != handler.resource_type:
    decisions[handler.logical_name] = ChangeKind.REPLACE
elif current.config_hash != handler.config_hash() and handler.can_update(current):
    decisions[handler.logical_name] = ChangeKind.UPDATE
elif current.config_hash != handler.config_hash():
    decisions[handler.logical_name] = ChangeKind.REPLACE
else:
    decisions[handler.logical_name] = ChangeKind.NOOP
""",
    )
    builder.paragraph(
        "Выполнение плана построено по паттерну Command. Листинг 4.2 демонстрирует update-команду: она получает ресурс из state, вызывает полиморфный метод update у обработчика, затем сохраняет обновленное состояние.",
    )
    add_inline_listing(
        builder,
        "Листинг 4.2 — Команда обновления ресурса",
        """
class UpdateResourceCommand(PlanCommand):
    handler: CloudResourceHandler
    reason: str

    def execute(self, facade, state, state_store) -> None:
        resource = state.get(self.handler.logical_name)
        if resource is None:
            raise ExecutionError(f"Resource '{self.logical_name}' is missing from state")
        updated = self.handler.update(facade, state, resource)
        state.put(updated)
        state_store.save(state)
""",
    )
    add_subsection(
        builder,
        "4.1 Пользовательский сценарий работы",
        """
Типовой сценарий начинается с подготовки YAML-манифеста. Пользователь указывает folder_id, zone_id, имена ресурсов, CIDR подсети, параметры виртуальной машины и путь к публичному SSH-ключу. Затем выполняется validate. Если манифест корректен, пользователь запускает plan и просматривает список изменений. После подтверждения apply создает или обновляет ресурсы. После успешного apply автоматически выводятся live-outputs.

Повторный запуск plan при неизменном манифесте должен показывать noop. Это важный критерий IaC-инструмента: повторное применение одного и того же описания не должно создавать лишних действий. Если пользователь изменяет, например, labels сети, план должен предложить update. Если изменяется image_family VM, план должен предложить replace. Такая дифференциация делает поведение инструмента более осмысленным.

Для завершения демонстрации используется destroy --confirm. Команда строит план удаления по state и выполняет его в обратном порядке зависимостей. После успешного destroy state очищается. Если часть ресурсов уже удалена вручную в облаке, delete-операции с NOT_FOUND считаются успешными, что делает destroy идемпотентным и удобным для восстановления после частичных сбоев.
""",
    )
    add_section(builder, "5 Тестирование программного средства", TESTING)
    add_section(builder, "Заключение", CONCLUSION, numbered=False)
    builder.paragraph("Список использованных источников", "heading0")
    for index, source in enumerate(SOURCES, start=1):
        builder.paragraph(f"{index}. {source}")


def add_appendix_code(builder: DocxBuilder) -> None:
    builder.page_break()
    builder.paragraph("Приложение А", "heading0")
    builder.paragraph("Листинг программного кода", "heading0")
    builder.paragraph(
        "В приложении приведены только ключевые фрагменты исходного кода, демонстрирующие основные объектно-ориентированные решения проекта: модель состояния, алгоритм выбора update/replace, команды плана, обработчик виртуальной машины, фасад облачного SDK и CLI-обработку ошибок.",
    )
    snippets = [
        ("src/iac_tool/state.py", 10, 70),
        ("src/iac_tool/planner.py", 78, 150),
        ("src/iac_tool/commands.py", 34, 125),
        ("src/iac_tool/resources.py", 30, 88),
        ("src/iac_tool/resources.py", 320, 455),
        ("src/iac_tool/facade.py", 740, 830),
        ("src/iac_tool/cli.py", 170, 220),
        ("tests/test_planner.py", 398, 492),
    ]
    for number, (relative, start, end) in enumerate(snippets, start=1):
        path = ROOT / relative
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        selected = lines[start - 1 : end]
        builder.paragraph(f"Листинг А.{number} — Фрагмент файла {relative}", "caption")
        for line in selected:
            builder.paragraph(line, "code")


def content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
        '<Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/>'
        '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        "</Types>"
    )


def root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )


def document_rels_xml(images: list[ImageAsset]) -> str:
    image_relationships = "".join(
        f'<Relationship Id="{asset.rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{asset.name}"/>'
        for asset in images
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdFooter1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
        f"{image_relationships}"
        "</Relationships>"
    )


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{NS_W}">'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
        '<w:sz w:val="28"/><w:szCs w:val="28"/>'
        '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
        '<w:spacing w:line="360" w:lineRule="auto"/>'
        '</w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        "</w:styles>"
    )


def settings_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:settings xmlns:w="{NS_W}">'
        '<w:updateFields w:val="true"/>'
        '<w:zoom w:percent="100"/>'
        '</w:settings>'
    )


def font_table_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:fonts xmlns:w="{NS_W}">'
        '<w:font w:name="Times New Roman"><w:family w:val="roman"/></w:font>'
        '<w:font w:name="Courier New"><w:family w:val="modern"/></w:font>'
        '</w:fonts>'
    )


def footer_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:ftr xmlns:w="{NS_W}">'
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '</w:p></w:ftr>'
    )


def build_docx() -> None:
    diagrams = build_diagrams()
    builder = DocxBuilder()
    title_pages(builder)
    add_main_text(builder, diagrams)
    add_appendix_code(builder)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types_xml())
        docx.writestr("_rels/.rels", root_rels_xml())
        docx.writestr("word/_rels/document.xml.rels", document_rels_xml(builder.images))
        docx.writestr("word/document.xml", builder.document_xml())
        docx.writestr("word/styles.xml", styles_xml())
        docx.writestr("word/settings.xml", settings_xml())
        docx.writestr("word/fontTable.xml", font_table_xml())
        docx.writestr("word/footer1.xml", footer_xml())
        for asset in builder.images:
            docx.writestr(f"word/media/{asset.name}", asset.data)


if __name__ == "__main__":
    build_docx()
    print(OUTPUT)
