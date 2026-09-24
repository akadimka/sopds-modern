"""
Genres Manager Module / Модуль управления жанрами

Manages genre hierarchy, associations, and genres.xml file.

/ Управление иерархией жанров, ассоциациями, genres.xml.
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

class GenreNode:
    """
    Represents a single genre node in the hierarchy.
    
    / Представляет один узел жанра в иерархии.
    """
    
    def __init__(self, name, parent=None):
        """Initialize genre node / Инициализация узла жанра."""
        self.name = name
        self.parent = parent
        self.children = []
        self.assigned = set()
        # Грубые правила по семейству кода (баг №82): «sf» → узел «Фантастика»
        # означает "любой код, чьё семейство до первого '_' — 'sf', если для
        # него самого нет точной ассоциации в `assigned`". Список, не set —
        # порядок сохраняется при сериализации ради предсказуемого редактирования,
        # хотя для самого разрешения кода порядок между УЗЛАМИ (а не внутри
        # списка одного узла) задаёт genre_priority_order, см. resolve_code().
        self.patterns = []

    def add_child(self, child):
        """
        Add child node if it doesn't already exist.
        
        Returns True if added, False if duplicate.
        
        / Добавить дочерний узел, если его еще нет.
        """
        # Check if child with this name already exists
        if any(c.name == child.name for c in self.children):
            return False  # Child already exists, don't add
        child.parent = self
        self.children.append(child)
        return True  # Successfully added

    def remove_child(self, child):
        """Remove child node / Удалить дочерний узел."""
        if child in self.children:
            self.children.remove(child)

class GenresManager:
    """
    Manages genre hierarchy and associations.

    / Управляет иерархией жанров и ассоциациями.
    """

    # Баг №114: раздел-маркер для кодов, дописанных в справочник
    # автоматически при скане (а не вручную куратором справочника) —
    # виден в Менеджере жанров как обычная секция, ждёт разметки.
    NEW_CODES_SECTION = 'Новые (не классифицировано)'
    # Есть хотя бы один кириллический символ — это не код жанра, а
    # ошибка/мусор метаданных (напр. русский текст в теге <genre> вместо
    # положенного латинского кода) — такое в справочник не заносим.
    _CYRILLIC_RE = re.compile(r'[Ѐ-ӿ]')

    def __init__(self, xml_path, reference_path=None):
        """Initialize genres manager / Инициализация менеджера жанров.

        `reference_path` — официальный справочник FB2-кодов (Django-
        фикстура `opds_catalog/fixtures/mygenres.json`, код → секция/
        подсекция таксономии) — статичен, не хранится в genres.xml,
        поэтому грузится один раз здесь, а не в `load()`. По умолчанию
        вычисляется относительно расположения этого файла (fb2parser
        полностью вендорен в этом репозитории, путь до соседнего
        `opds_catalog` стабилен). Явный параметр — для тестов (свой,
        маленький справочник) или отсутствия файла (пустой `{}` —
        резолвер просто не получает эту ступень, не падает).
        """
        self.xml_path = Path(xml_path)
        self.root_nodes = []
        self.section_map = {}
        self.excluded_codes = set()
        self.reference_path = Path(reference_path) if reference_path else self._default_reference_path()
        self._reference = self._load_reference(self.reference_path)
        self.load()

    @staticmethod
    def _default_reference_path():
        return Path(__file__).resolve().parent.parent / 'opds_catalog' / 'fixtures' / 'mygenres.json'

    @staticmethod
    def _load_reference(path):
        """код(lower) -> (section, subsection) из Django-фикстуры справочника."""
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
        except Exception:
            return {}
        reference = {}
        for item in data:
            fields = item.get('fields', {}) if isinstance(item, dict) else {}
            code = (fields.get('genre') or '').strip().lower()
            if code:
                reference[code] = (fields.get('section') or '', fields.get('subsection') or '')
        return reference

    def set_xml_path(self, xml_path):
        """Set XML file path / Установить путь к файлу XML."""
        self.xml_path = Path(xml_path)
        self.load()

    def register_discovered_codes(self, codes):
        """При каждом скане жанров (кнопки "Scan genres"/"Start scan") —
        ранее неизвестные справочнику коды дописываются прямо в
        `mygenres.json` под служебным разделом `NEW_CODES_SECTION`, чтобы
        сразу попасть в Менеджер жанров для разметки, а не потеряться.
        Баг №114.

        Пропускает: уже известные коды (есть в справочнике — неважно,
        официальный раздел или ранее сюда же дописанный); коды с
        кириллицей (это не код жанра, а мусор/ошибка метаданных, а не
        новый жанровый код — раз он не на латинице, значит вообще не по
        правилам FB2-таксономии).
        """
        new_codes = []
        seen = set()
        for code in codes:
            code = (code or '').strip()
            if not code:
                continue
            code_l = code.lower()
            if code_l in self._reference or code_l in seen:
                continue
            if self._CYRILLIC_RE.search(code):
                continue
            seen.add(code_l)
            new_codes.append(code_l)
        if not new_codes:
            return
        self._append_to_reference_file(new_codes)
        for code_l in new_codes:
            self._reference[code_l] = (self.NEW_CODES_SECTION, '')

    def _append_to_reference_file(self, codes):
        """Дописать новые записи в Django-фикстуру справочника (тот же
        JSON-формат, что уже читает `_load_reference()`) — атомарно,
        через временный файл, как и `save()` делает для genres.xml."""
        try:
            data = json.loads(Path(self.reference_path).read_text(encoding='utf-8'))
        except Exception:
            data = []
        max_pk = 0
        for item in data:
            if isinstance(item, dict):
                try:
                    max_pk = max(max_pk, int(item.get('pk', 0)))
                except (TypeError, ValueError):
                    pass
        for code in codes:
            max_pk += 1
            data.append({
                'model': 'opds_catalog.genre',
                'pk': max_pk,
                'fields': {'genre': code, 'section': self.NEW_CODES_SECTION, 'subsection': ''},
            })
        tmp_path = Path(str(self.reference_path) + '.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        tmp_path.replace(self.reference_path)

    def load(self):
        """Load genres from XML file / Загрузить жанры из файла XML."""
        self.root_nodes.clear()
        self.section_map = {}
        self.excluded_codes = set()
        if not self.xml_path.exists():
            return
        tree = ET.parse(self.xml_path)
        root = tree.getroot()
        section_map_elem = root.find('section_map')
        if section_map_elem is not None:
            for m in section_map_elem.findall('map'):
                section = m.attrib.get('section')
                genre = m.attrib.get('genre')
                if section and genre:
                    self.section_map[section] = genre
        excluded_elem = root.find('excluded_codes')
        if excluded_elem is not None:
            self.excluded_codes = set(
                c.text.strip().lower() for c in excluded_elem.findall('code') if c.text and c.text.strip()
            )
        def parse_node(elem, parent=None):
            node = GenreNode(elem.attrib['name'], parent)
            assigned = elem.find('assigned')
            if assigned is not None:
                node.assigned = set(a.text.strip() for a in assigned.findall('genre') if a.text)
            patterns = elem.find('patterns')
            if patterns is not None:
                node.patterns = [p.text.strip() for p in patterns.findall('pattern') if p.text and p.text.strip()]
            for child_elem in elem.findall('genre'):
                node.add_child(parse_node(child_elem, node))
            return node
        for elem in root.findall('genre'):
            self.root_nodes.append(parse_node(elem))

    def save(self):
        """Save genres to XML file / Сохранить жанры в файл XML."""
        def node_to_elem(node):
            elem = ET.Element('genre', {'name': node.name})
            if node.assigned:
                assigned_elem = ET.SubElement(elem, 'assigned')
                for genre_str in sorted(node.assigned):  # Сортируем для консистентности
                    g = ET.SubElement(assigned_elem, 'genre')
                    g.text = genre_str
            if node.patterns:
                patterns_elem = ET.SubElement(elem, 'patterns')
                for pattern_str in node.patterns:
                    p = ET.SubElement(patterns_elem, 'pattern')
                    p.text = pattern_str
            for child in node.children:
                elem.append(node_to_elem(child))
            return elem
        root = ET.Element('genres')
        if self.section_map:
            section_map_elem = ET.SubElement(root, 'section_map')
            for section in sorted(self.section_map):
                ET.SubElement(section_map_elem, 'map', {'section': section, 'genre': self.section_map[section]})
        if self.excluded_codes:
            excluded_elem = ET.SubElement(root, 'excluded_codes')
            for code in sorted(self.excluded_codes):
                c = ET.SubElement(excluded_elem, 'code')
                c.text = code
        for node in self.root_nodes:
            root.append(node_to_elem(node))
        tree = ET.ElementTree(root)
        tmp_path = self.xml_path.with_suffix('.tmp')
        tree.write(tmp_path, encoding='utf-8', xml_declaration=True)
        tmp_path.replace(self.xml_path)

    def find_node(self, name, nodes=None):
        if nodes is None:
            nodes = self.root_nodes
        for node in nodes:
            if node.name == name:
                return node
            found = self.find_node(name, node.children)
            if found:
                return found
        return None

    def associate(self, genre_str, main_genre):
        self.load()  # Загрузить актуальные данные из файла
        node = self.find_node(main_genre)
        if node:
            # Проверяем, есть ли уже такая ассоциация
            if genre_str not in node.assigned:
                node.assigned.add(genre_str)
                self.save()

    def associate_many(self, pairs):
        """Пакетная версия `associate()` — один `load()`, N мутаций в
        памяти, один `save()` (только если хоть что-то реально
        изменилось) — вместо до N полных циклов чтения+перезаписи
        `genres.xml`, если вызывать `associate()` в цикле (баг №102:
        `genre_scan_assign()` применяет это к десяткам кодов за один
        батч).

        Args:
            pairs: итерируемое пар (genre_str, main_genre).
        """
        self.load()
        changed = False
        for genre_str, main_genre in pairs:
            node = self.find_node(main_genre)
            if node and genre_str not in node.assigned:
                node.assigned.add(genre_str)
                changed = True
        if changed:
            self.save()

    def remove_association(self, genre_str, main_genre):
        self.load()  # Загрузить актуальные данные из файла
        node = self.find_node(main_genre)
        if node and genre_str in node.assigned:
            node.assigned.remove(genre_str)
            self.save()

    def associate_pattern(self, pattern, main_genre):
        """Грубое правило по семейству кода (баг №82) — см. `resolve_code()`.

        `pattern` сравнивается с частью необработанного кода жанра ДО первого
        `_` (например "sf" для "sf_action"/"sf_cyberpunk"), без учёта регистра.
        """
        pattern = (pattern or '').strip().lower()
        if not pattern:
            return
        self.load()
        node = self.find_node(main_genre)
        if node and pattern not in node.patterns:
            node.patterns.append(pattern)
            self.save()

    def remove_pattern_association(self, pattern, main_genre):
        pattern = (pattern or '').strip().lower()
        self.load()
        node = self.find_node(main_genre)
        if node and pattern in node.patterns:
            node.patterns.remove(pattern)
            self.save()

    def _ordered_nodes(self, priority_order):
        """Все узлы дерева, отсортированные по `priority_order` (имена
        корневых жанров в порядке приоритета — см. баг №82). Узлы, чьё имя
        не входит в `priority_order` (в т.ч. неё-корневые дочерние узлы),
        идут последними, в естественном порядке обхода дерева.
        """
        flat = []
        def _walk(nodes):
            for n in nodes:
                flat.append(n)
                _walk(n.children)
        _walk(self.root_nodes)
        order_index = {name: i for i, name in enumerate(priority_order or [])}
        return sorted(flat, key=lambda n: order_index.get(n.name, len(order_index)))

    def resolve_code(self, code, priority_order=None):
        """Разрешить ОДИН сырой код жанра (`<genre>` из FB2) в корневой жанр.

        Баг №82 (docs/quality-roadmap.md): трёхуровневое правило —
        1) точная ассоциация (`assigned`) побеждает всегда, если есть;
        2) иначе — код есть в официальном справочнике FB2-таксономии
           (`opds_catalog/fixtures/mygenres.json`) и его секция
           сопоставлена жанру через `section_map` (баг №113: объективный
           источник вместо слепо накопленных вручную ассоциаций);
        3) иначе — грубое совпадение по семейству кода (часть до первого
           `_`, например "sf" для "sf_cyberpunk") с `patterns` узла.
        При конфликте между несколькими узлами на ступени 1/3 (редкий
        случай — код ассоциирован/подпадает под правило сразу нескольких
        корневых жанров) побеждает узел, который раньше встречается в
        `priority_order`; ступень 2 сама по себе однозначна (одна секция
        мапится максимум на один жанр), приоритет тут не участвует.

        Returns:
            Tuple[Optional[str], Optional[bool]]: (имя корневого жанра, был
            ли результат НАДЁЖНЫМ — точная ассоциация или справочник,
            баг №84/№113) — в обоих случаях `True`, поскольку это не
            гадание. `(None, None)`, если код не разрешился вообще.
            Различие с `False` (ступень 3) важно для UI: там результат —
            лишь предположение по семейству кода, которое ещё стоит
            проверить.
        """
        code_l = (code or '').strip().lower()
        if not code_l:
            return None, None
        nodes = self._ordered_nodes(priority_order)
        for node in nodes:
            if code_l in node.assigned:
                return node.name, True
        section, _ = self._reference.get(code_l, (None, None))
        if section:
            mapped_genre = self.section_map.get(section)
            if mapped_genre and self.find_node(mapped_genre):
                return mapped_genre, True
        family = code_l.split('_', 1)[0]
        for node in nodes:
            if family in node.patterns:
                return node.name, False
        return None, None

    def is_discriminating_code(self, code):
        """Стоит ли код доверять как жанровый сигнал вообще (баг №113).

        Используется вызывающими перед тем, как ЗАПОМИНАТЬ код как новую
        точную ассоциацию (`genre_scan_assign()`) — не самим `resolve_code()`.
        `False` для кода из `excluded_codes` (заведомо не жанр, а формат
        публикации — "compilation", "collection" и т.п., которых часто и
        в справочнике-то нет) и для кода, чья секция в справочнике
        известна, но НЕ сопоставлена ни одному жанру через `section_map`
        (например "Прочее"/"Unknown genre" — пока не размечено).
        Неизвестный справочнику код по умолчанию `True` (не в чём
        разубеждать — раньше именно так и работало).
        """
        code_l = (code or '').strip().lower()
        if not code_l:
            return False
        if code_l in self.excluded_codes:
            return False
        section, _ = self._reference.get(code_l, (None, None))
        if section:
            return bool(self.section_map.get(section))
        return True

    def list_sections(self):
        """Все секции официального справочника с числом кодов, текущим
        сопоставленным жанром и подсказкой-тултипом (какие именно коды
        стоят за числом, сгруппированные по подсекции) — для UI-таблицы
        `section_map`."""
        by_section: dict = {}
        for code, (section, subsection) in self._reference.items():
            if section:
                by_section.setdefault(section, []).append((subsection or '', code))
        result = []
        for section in sorted(by_section):
            items = sorted(by_section[section])
            by_subsection: dict = {}
            for subsection, code in items:
                by_subsection.setdefault(subsection or '—', []).append(code)
            tooltip = "\n".join(
                f"{subsection}: {', '.join(codes)}"
                for subsection, codes in sorted(by_subsection.items())
            )
            result.append({
                "section": section,
                "count": len(items),
                "genre": self.section_map.get(section),
                "codes_tooltip": tooltip,
            })
        return result

    def list_reference_codes(self):
        """Все отдельные коды официального справочника с их секцией и
        ТЕКУЩИМ эффективным жанром (`resolve_code()` — точная ассоциация
        или section_map, что бы ни сработало) — для UI-таблицы
        «по кодам» (баг №113, продолжение: раздел-целиком слишком грубая
        гранулярность для смешанных разделов вроде "Unknown genre")."""
        result = []
        for code in sorted(self._reference):
            section, subsection = self._reference[code]
            genre, _is_exact = self.resolve_code(code)
            result.append({
                "code": code,
                "section": section,
                "subsection": subsection,
                "genre": genre,
            })
        return result

    def set_code_association(self, code, genre_name):
        """Назначить (или снять, если `genre_name` пусто) точную
        ассоциацию код→жанр — в отличие от `associate()`/
        `remove_association()`, сама находит и убирает код из ЛЮБОГО
        узла, где он мог быть назначен раньше (используется UI-таблицей
        «по кодам», где один select задаёт итоговый жанр целиком, а не
        отдельно добавляет/убирает)."""
        code = (code or '').strip()
        if not code:
            return
        self.load()
        def _walk(nodes):
            for n in nodes:
                n.assigned.discard(code)
                _walk(n.children)
        _walk(self.root_nodes)
        genre_name = (genre_name or '').strip()
        if genre_name:
            node = self.find_node(genre_name)
            if node:
                node.assigned.add(code)
        self.save()

    def get_section_map(self):
        return dict(self.section_map)

    def set_section_mapping(self, section, genre_name):
        """Сопоставить (или убрать сопоставление, если `genre_name` пусто)
        официальную секцию справочника с жанром пользователя."""
        section = (section or '').strip()
        if not section:
            return
        self.load()
        genre_name = (genre_name or '').strip()
        if genre_name:
            self.section_map[section] = genre_name
        else:
            self.section_map.pop(section, None)
        self.save()

    def get_excluded_codes(self):
        return set(self.excluded_codes)

    def add_excluded_code(self, code):
        code = (code or '').strip().lower()
        if not code:
            return
        self.load()
        if code not in self.excluded_codes:
            self.excluded_codes.add(code)
            self.save()

    def remove_excluded_code(self, code):
        code = (code or '').strip().lower()
        self.load()
        if code in self.excluded_codes:
            self.excluded_codes.discard(code)
            self.save()

    def clear_all_assigned(self):
        """Обнулить точные ассоциации (`assigned`) у ВСЕХ узлов дерева —
        баг №113 (полный сброс накопленного слепым обучением шума).
        `patterns`/`section_map`/`excluded_codes` не трогает."""
        self.load()
        def _walk(nodes):
            for n in nodes:
                n.assigned = set()
                _walk(n.children)
        _walk(self.root_nodes)
        self.save()

    def resolve_combo(self, combo, priority_order=None):
        """Разрешить КОМБИНАЦИЮ жанров (строку через запятую, как её
        возвращает `scan_fb2_genres()`/`metadata_genre`) в один корневой
        жанр — баг №82.

        Каждый код комбинации разрешается независимо (`resolve_code`);
        среди успешно разрешившихся выбирается один по `priority_order`.
        Коды, которые не разрешились вообще, не блокируют результат — если
        разрешился хотя бы один код, он и используется (см. обсуждение в
        docs/quality-roadmap.md).

        Returns:
            Tuple[Optional[str], Optional[bool]]: (имя корневого жанра, был
            ли результат подтверждён ТОЧНОЙ ассоциацией хотя бы одного кода
            комбинации — баг №84). Если победивший жанр получен только по
            грубому правилу семейства (ни один код не имеет точной
            ассоциации именно на этот жанр) — `False`. `(None, None)`, если
            НИ ОДИН код не разрешился.
        """
        codes = [c.strip() for c in (combo or '').split(',') if c.strip()]
        # genre -> уже встречалась ли для него точная ассоциация среди кодов комбинации
        resolved: dict = {}
        order = []
        for code in codes:
            genre, is_exact = self.resolve_code(code, priority_order)
            if not genre:
                continue
            if genre not in resolved:
                resolved[genre] = False
                order.append(genre)
            if is_exact:
                resolved[genre] = True
        if not order:
            return None, None
        if len(order) == 1:
            winner = order[0]
        else:
            winner = None
            for genre in (priority_order or []):
                if genre in resolved:
                    winner = genre
                    break
            if winner is None:
                winner = order[0]
        return winner, resolved[winner]

    def _siblings_of(self, node):
        """Список-контейнер, в котором физически лежит node (root_nodes или node.parent.children)."""
        return node.parent.children if node.parent else self.root_nodes

    def add_node(self, name, parent_name=None):
        """Добавить новый узел жанра.

        Args:
            name: название нового жанра.
            parent_name: имя родителя (None — добавить корневым узлом).

        Returns:
            True при успехе, False если имя пустое, уже существует в дереве,
            либо parent_name не найден.
        """
        name = (name or '').strip()
        if not name or self.find_node(name):
            return False
        if parent_name:
            parent = self.find_node(parent_name)
            if not parent:
                return False
            parent.add_child(GenreNode(name, parent))
        else:
            self.root_nodes.append(GenreNode(name))
        self.save()
        return True

    def rename_node(self, old_name, new_name):
        """Переименовать узел. Возвращает False, если old_name не найден
        либо new_name уже занято другим узлом дерева."""
        new_name = (new_name or '').strip()
        if not new_name:
            return False
        node = self.find_node(old_name)
        if not node:
            return False
        if new_name != node.name and self.find_node(new_name):
            return False
        node.name = new_name
        # section_map хранит ИМЯ жанра как строку (баг №113) — без этого
        # переименование узла молча "отвязало" бы от него уже настроенные
        # сопоставления секций справочника.
        for section, genre in list(self.section_map.items()):
            if genre == old_name:
                self.section_map[section] = new_name
        self.save()
        return True

    def delete_node(self, name):
        """Удалить узел вместе со всеми дочерними (каскадно)."""
        node = self.find_node(name)
        if not node:
            return False
        # Убираем сопоставления section_map на удаляемый узел — иначе они
        # тихо "протухают" (resolve_code() и так защищён find_node()-
        # проверкой, но лучше не копить мёртвые записи).
        for section, genre in list(self.section_map.items()):
            if genre == name:
                del self.section_map[section]
        self._siblings_of(node).remove(node)
        self.save()
        return True

    def move_node(self, name, direction):
        """Переместить узел в дереве.

        direction:
            'up'/'down'   — поменять местами с соседним узлом на том же уровне.
            'indent'      — сделать дочерним последнего предыдущего узла-соседа.
            'outdent'     — поднять на один уровень выше (стать соседом своего родителя).

        Returns:
            True при успехе, False если операция невозможна (нет нужного соседа,
            узел не найден, попытка сделать дочерним себя же и т.п.).
        """
        node = self.find_node(name)
        if not node:
            return False

        siblings = self._siblings_of(node)
        idx = siblings.index(node)

        if direction == 'up':
            if idx == 0:
                return False
            siblings[idx - 1], siblings[idx] = siblings[idx], siblings[idx - 1]

        elif direction == 'down':
            if idx == len(siblings) - 1:
                return False
            siblings[idx + 1], siblings[idx] = siblings[idx], siblings[idx + 1]

        elif direction == 'indent':
            if idx == 0:
                return False  # нет предыдущего соседа, к которому можно "прижаться"
            new_parent = siblings[idx - 1]
            # Проверяем ДО мутации: если у нового родителя уже есть дочерний
            # узел с таким именем, add_child() откажет молча, а node к тому
            # моменту уже будет удалён из старого списка — то есть потерян
            # из дерева. Поэтому валидируем заранее и ничего не трогаем при конфликте.
            if any(c.name == node.name for c in new_parent.children):
                return False
            siblings.pop(idx)
            new_parent.add_child(node)

        elif direction == 'outdent':
            old_parent = node.parent
            if not old_parent:
                return False  # уже корневой узел — выше некуда
            grandparent_list = self._siblings_of(old_parent)
            # Проверяем ДО мутации: узел не должен конфликтовать по имени
            # с уже существующими соседями на уровне, куда он поднимается
            # (сам old_parent из проверки исключаем — это не тот уровень).
            if any(c.name == node.name for c in grandparent_list if c is not old_parent):
                return False
            siblings.pop(idx)
            node.parent = old_parent.parent
            grandparent_list.insert(grandparent_list.index(old_parent) + 1, node)

        else:
            return False

        self.save()
        return True

    def get_all_genres(self):
        """
        Получить список всех жанров в плоском формате.
        
        Returns:
            List[str] - список всех названий жанров
        """
        genres = []
        
        def collect_genres(nodes):
            for node in nodes:
                genres.append(node.name)
                if node.children:
                    collect_genres(node.children)
        
        collect_genres(self.root_nodes)
        return genres