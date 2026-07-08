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