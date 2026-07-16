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
            siblings.pop(idx)
            new_parent.add_child(node)

        elif direction == 'outdent':
            old_parent = node.parent
            if not old_parent:
                return False  # уже корневой узел — выше некуда
            siblings.pop(idx)
            grandparent_list = self._siblings_of(old_parent)
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