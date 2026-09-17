"""
Genres Manager Module / Модуль управления жанрами

Manages genre hierarchy, associations, and genres.xml file.

/ Управление иерархией жанров, ассоциациями, genres.xml.
"""
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
    
    def __init__(self, xml_path):
        """Initialize genres manager / Инициализация менеджера жанров."""
        self.xml_path = Path(xml_path)
        self.root_nodes = []
        self.load()

    def set_xml_path(self, xml_path):
        """Set XML file path / Установить путь к файлу XML."""
        self.xml_path = Path(xml_path)
        self.load()

    def load(self):
        """Load genres from XML file / Загрузить жанры из файла XML."""
        self.root_nodes.clear()
        if not self.xml_path.exists():
            return
        tree = ET.parse(self.xml_path)
        root = tree.getroot()
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

        Баг №82 (docs/quality-roadmap.md): двухуровневое правило —
        1) точная ассоциация (`assigned`) побеждает всегда, если есть;
        2) иначе — грубое совпадение по семейству кода (часть до первого
           `_`, например "sf" для "sf_cyberpunk") с `patterns` узла.
        При конфликте между несколькими узлами (редкий случай — код
        ассоциирован/подпадает под правило сразу нескольких корневых
        жанров) побеждает узел, который раньше встречается в
        `priority_order`.

        Returns:
            Tuple[Optional[str], Optional[bool]]: (имя корневого жанра, был
            ли это ТОЧНОЙ ассоциацией — баг №84). `(None, None)`, если код
            не разрешился вообще. Различие важно для UI: точная ассоциация
            означает "пользователь уже подтверждал именно этот код раньше" —
            это НЕ то же самое, что грубое совпадение по семейству кода
            (предположение, которое ещё стоит проверить).
        """
        code_l = (code or '').strip().lower()
        if not code_l:
            return None, None
        nodes = self._ordered_nodes(priority_order)
        for node in nodes:
            if code_l in node.assigned:
                return node.name, True
        family = code_l.split('_', 1)[0]
        for node in nodes:
            if family in node.patterns:
                return node.name, False
        return None, None

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
        self.save()
        return True

    def delete_node(self, name):
        """Удалить узел вместе со всеми дочерними (каскадно)."""
        node = self.find_node(name)
        if not node:
            return False
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