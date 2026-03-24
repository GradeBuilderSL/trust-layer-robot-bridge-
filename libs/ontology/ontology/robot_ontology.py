"""
Robot Ontology — базовые типы и отношения для мира робота.
"""
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple


class ObjectType(Enum):
    """Типы объектов, которые может распознать робот."""
    HUMAN = "human"
    TABLE = "table"
    CHAIR = "chair"
    WALL = "wall"
    DOOR = "door"
    WINDOW = "window"
    BOX = "box"
    ROBOT = "robot"
    PLANT = "plant"
    SCREEN = "screen"
    UNKNOWN = "unknown"


class SpatialRelation(Enum):
    """Пространственные отношения относительно робота."""
    LEFT = "left"
    RIGHT = "right"
    FRONT = "front"
    BACK = "back"
    NEAR = "near"
    FAR = "far"


class ObjectInstance:
    """Экземпляр объекта в мире."""
    
    def __init__(
        self,
        object_id: str,
        object_type: ObjectType,
        position: Tuple[float, float, float],  # (x, y, z) в мировых координатах
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.object_id = object_id
        self.object_type = object_type
        self.position = position
        self.confidence = confidence
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "object_id": self.object_id,
            "object_type": self.object_type.value,
            "position": self.position,
            "confidence": self.confidence,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObjectInstance":
        return cls(
            object_id=data["object_id"],
            object_type=ObjectType(data["object_type"]),
            position=tuple(data["position"]),
            confidence=data.get("confidence", 1.0),
            metadata=data.get("metadata", {})
        )


class RobotPose:
    """Поза робота (положение и ориентация)."""
    
    def __init__(
        self,
        x: float,
        y: float,
        z: float,
        theta: float,  # Ориентация в радианах (0 = смотрит вдоль оси X)
        timestamp: float = 0.0
    ):
        self.x = x
        self.y = y
        self.z = z
        self.theta = theta
        self.timestamp = timestamp
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "theta": self.theta,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RobotPose":
        return cls(
            x=data["x"],
            y=data["y"],
            z=data["z"],
            theta=data.get("theta", 0.0),
            timestamp=data.get("timestamp", 0.0)
        )


class SpatialDescription:
    """Пространственное описание объектов относительно робота."""
    
    def __init__(self):
        self.objects_by_relation: Dict[SpatialRelation, List[Tuple[ObjectInstance, float]]] = {
            SpatialRelation.LEFT: [],
            SpatialRelation.RIGHT: [],
            SpatialRelation.FRONT: [],
            SpatialRelation.BACK: [],
            SpatialRelation.NEAR: [],
            SpatialRelation.FAR: []
        }
    
    def add_object(self, relation: SpatialRelation, obj: ObjectInstance, distance: float):
        """Добавить объект с указанным отношением и расстоянием."""
        self.objects_by_relation[relation].append((obj, distance))
    
    def get_description(self) -> str:
        """Сформировать естественно-языковое описание."""
        parts = []
        
        for relation in [
            SpatialRelation.LEFT, SpatialRelation.RIGHT,
            SpatialRelation.FRONT, SpatialRelation.BACK
        ]:
            objects = self.objects_by_relation[relation]
            if objects:
                # Сортируем по расстоянию (ближайшие первыми)
                objects.sort(key=lambda x: x[1])
                obj_names = []
                for obj, distance in objects:
                    name = obj.object_type.value
                    if obj.metadata.get("name"):
                        name = obj.metadata["name"]
                    
                    # Округляем расстояние до 0.1м
                    distance_str = f"{distance:.1f}"
                    obj_names.append(f"{name} ({distance_str}м)")
                
                if obj_names:
                    relation_text = {
                        SpatialRelation.LEFT: "слева",
                        SpatialRelation.RIGHT: "справа", 
                        SpatialRelation.FRONT: "впереди",
                        SpatialRelation.BACK: "позади"
                    }[relation]
                    parts.append(f"{relation_text} от тебя: {', '.join(obj_names)}")
        
        # Добавляем ближайшие/дальние объекты, если есть
        near_objects = self.objects_by_relation[SpatialRelation.NEAR]
        far_objects = self.objects_by_relation[SpatialRelation.FAR]
        
        if near_objects:
            near_names = [obj.object_type.value for obj, _ in near_objects[:3]]  # Первые 3 ближайших
            parts.append(f"близко: {', '.join(near_names)}")
        
        if far_objects:
            far_names = [obj.object_type.value for obj, _ in far_objects[:3]]  # Первые 3 дальних
            parts.append(f"далеко: {', '.join(far_names)}")
        
        if not parts:
            return "Я не вижу объектов вокруг."
        
        return " ".join(parts)


def compute_relative_position(
    object_pos: Tuple[float, float, float],
    robot_pose: RobotPose
) -> Tuple[float, float, float]:
    """
    Преобразовать мировые координаты объекта в координаты относительно робота.
    
    Args:
        object_pos: (x, y, z) в мировых координатах
        robot_pose: поза робота
        
    Returns:
        (x_robot, y_robot, z_robot) - координаты в системе робота
        (x_robot направлен вперед, y_robot - влево, z_robot - вверх)
    """
    import math
    
    # Смещение объекта относительно робота
    dx = object_pos[0] - robot_pose.x
    dy = object_pos[1] - robot_pose.y
    dz = object_pos[2] - robot_pose.z
    
    # Поворот на угол -theta (переход в систему координат робота)
    cos_theta = math.cos(-robot_pose.theta)
    sin_theta = math.sin(-robot_pose.theta)
    
    x_robot = dx * cos_theta - dy * sin_theta
    y_robot = dx * sin_theta + dy * cos_theta
    z_robot = dz
    
    return x_robot, y_robot, z_robot


def determine_spatial_relation(
    object_pos_robot: Tuple[float, float, float],
    near_threshold: float = 2.0,
    far_threshold: float = 5.0
) -> SpatialRelation:
    """
    Определить пространственное отношение объекта относительно робота.
    
    Args:
        object_pos_robot: (x, y, z) в системе координат робота
        near_threshold: расстояние в метрах для "близко"
        far_threshold: расстояние в метрах для "далеко"
        
    Returns:
        SpatialRelation
    """
    x, y, z = object_pos_robot
    
    # Сначала определяем расстояние
    distance = math.sqrt(x**2 + y**2 + z**2)
    
    if distance < near_threshold:
        return SpatialRelation.NEAR
    elif distance > far_threshold:
        return SpatialRelation.FAR
    
    # Для объектов на среднем расстоянии определяем квадрант
    # Используем абсолютные значения для определения доминирующего направления
    if abs(y) > abs(x):
        # Доминирует смещение по Y (влево/вправо)
        return SpatialRelation.LEFT if y > 0 else SpatialRelation.RIGHT
    else:
        # Доминирует смещение по X (вперед/назад)
        return SpatialRelation.FRONT if x > 0 else SpatialRelation.BACK