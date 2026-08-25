"""
Управление "плотами" (платформами) игроков.

Расположение: все платформы в ряд вдоль оси X, на одной Z-линии.
Платформа: 40x40, с окантовкой другим блоком по краю (1 блок толщиной),
внутри - трава. Между платформами - зазор 20 блоков (край в край).

    |<--40-->|<--20-->|<--40-->|<--20-->|<--40-->|
    [ plot 0 ]  gap     [ plot 1 ]  gap    [ plot 2 ] ...
"""

PLOT_SIZE = 40          # ширина/глубина платформы
GAP = 20                 # зазор между краями соседних платформ
SPACING = PLOT_SIZE + GAP  # расстояние между стартом соседних платформ (60)

PLOT_Y = 64               # высота, на которой лежит платформа (уровень травы)
SPAWN_Y = PLOT_Y + 1       # игрок спавнится на 1 блок выше травы

BORDER_BLOCK_ID = "minecraft:stone_bricks"
GRASS_BLOCK_ID = "minecraft:grass_block"
GLASS_BLOCK_ID = "minecraft:glass"
AGREE_BLOCK_ID = "minecraft:emerald_block"

# --- Стеклянная комната регистрации (высоко в небе, над платформой игрока) ---
LOBBY_Y = 200                 # высота пола комнаты
LOBBY_RADIUS = 2                # комната 5x5 в плане (радиус 2 от центра)
LOBBY_HEIGHT = 4                 # высота потолка над полом
LOBBY_SPAWN_Y = LOBBY_Y + 1        # игрок стоит на полу комнаты


class Plot:
    """Описывает одну платформу: её границы в мировых координатах."""

    def __init__(self, index: int, owner: str):
        self.index = index
        self.owner = owner
        self.active = True
        # X-координата левого-нижнего угла платформы (Z всегда одна и та же)
        self.origin_x = index * SPACING
        self.origin_z = 0

    @property
    def center_x(self) -> int:
        return self.origin_x + PLOT_SIZE // 2

    @property
    def center_z(self) -> int:
        return self.origin_z + PLOT_SIZE // 2

    def spawn_position(self):
        """Точка спавна игрока - центр его платформы, чуть выше травы."""
        return (self.center_x + 0.5, SPAWN_Y, self.center_z + 0.5)

    def lobby_spawn_position(self):
        """Точка спавна в стеклянной комнате регистрации (высоко в небе)."""
        return (self.center_x + 0.5, LOBBY_SPAWN_Y, self.center_z + 0.5)

    def agree_block_position(self):
        """Координаты специального блока (изумрудный блок в полу), клик по
        которому означает "согласен с правилами"."""
        return (self.center_x + 1, LOBBY_Y, self.center_z)

    def contains(self, x: int, z: int) -> bool:
        return (self.origin_x <= x < self.origin_x + PLOT_SIZE
                and self.origin_z <= z < self.origin_z + PLOT_SIZE)

    def block_at(self, x: int, z: int) -> str | None:
        """Какой блок должен быть в колонке (x, z) на уровне PLOT_Y.
        Возвращает None, если (x, z) вне этой платформы или платформа
        деактивирована (игрок вышел, платформа "убрана")."""
        if not self.active:
            return None
        if not self.contains(x, z):
            return None
        local_x = x - self.origin_x
        local_z = z - self.origin_z
        is_border = (
            local_x == 0 or local_x == PLOT_SIZE - 1
            or local_z == 0 or local_z == PLOT_SIZE - 1
        )
        return BORDER_BLOCK_ID if is_border else GRASS_BLOCK_ID

    def lobby_block_at(self, x: int, y: int, z: int) -> str | None:
        """Блок стеклянной комнаты регистрации в точке (x,y,z), либо None."""
        if not self.active:
            return None
        dx = x - self.center_x
        dz = z - self.center_z
        dy = y - LOBBY_Y
        if abs(dx) > LOBBY_RADIUS or abs(dz) > LOBBY_RADIUS:
            return None
        if dy < 0 or dy > LOBBY_HEIGHT:
            return None

        if (x, y, z) == self.agree_block_position():
            return AGREE_BLOCK_ID

        is_floor_or_ceiling = (dy == 0 or dy == LOBBY_HEIGHT)
        is_wall = (abs(dx) == LOBBY_RADIUS or abs(dz) == LOBBY_RADIUS)
        if is_floor_or_ceiling or is_wall:
            return GLASS_BLOCK_ID
        return None  # внутри комнаты - воздух


class PlotManager:
    """Хранит все выданные плоты и выдаёт новые по мере захода игроков."""

    def __init__(self):
        self._plots_by_owner: dict[str, Plot] = {}
        self._plots_in_order: list[Plot] = []
        self._lock = __import__("threading").Lock()

    def get_or_assign(self, username: str) -> Plot:
        with self._lock:
            existing = self._plots_by_owner.get(username)
            if existing is not None:
                existing.active = True
                return existing
            # переиспользуем первый освобождённый (неактивный) слот, если есть
            for plot in self._plots_in_order:
                if not plot.active:
                    plot.active = True
                    plot.owner = username
                    self._plots_by_owner[username] = plot
                    return plot
            plot = Plot(index=len(self._plots_in_order), owner=username)
            self._plots_by_owner[username] = plot
            self._plots_in_order.append(plot)
            return plot

    def release(self, username: str):
        """Деактивирует платформу игрока при выходе (она станет пустотой),
        слот освобождается для переиспользования следующим новым игроком."""
        with self._lock:
            plot = self._plots_by_owner.pop(username, None)
            if plot is not None:
                plot.active = False

    def all_plots(self):
        return list(self._plots_in_order)

    def block_at(self, x: int, z: int) -> str | None:
        """Ищет, какой платформе принадлежит колонка (x, z) и какой там блок.
        Пустота (None), если колонка ничья."""
        # платформы не пересекаются и идут по возрастанию X, так что можно
        # сразу вычислить индекс платформы по X, не перебирая все
        if x < 0:
            return None
        approx_index = x // SPACING
        for idx in (approx_index - 1, approx_index, approx_index + 1):
            if 0 <= idx < len(self._plots_in_order):
                plot = self._plots_in_order[idx]
                block = plot.block_at(x, z)
                if block:
                    return block
        return None

    def lobby_block_at(self, x: int, y: int, z: int) -> str | None:
        """То же самое, но для стеклянной комнаты регистрации (высоко в небе)."""
        if y < LOBBY_Y - 1 or y > LOBBY_Y + LOBBY_HEIGHT + 1 or x < 0:
            return None
        approx_index = x // SPACING
        for idx in (approx_index - 1, approx_index, approx_index + 1):
            if 0 <= idx < len(self._plots_in_order):
                plot = self._plots_in_order[idx]
                block = plot.lobby_block_at(x, y, z)
                if block:
                    return block
        return None
