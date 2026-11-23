"""
tests/gesture_hands.py — la mano de las pruebas, la de verdad.

POR QUE ESTE FICHERO EXISTE
    Las pruebas de gestos se escribieron con una mano inventada: veinte
    y un puntos dibujados a mano, con los dedos abiertos y separados.
    Esa mano no es la mano de nadie, y las lineas del producto se
    midieron contra ella. Por eso un pellizco de verdad — el indice a
    media distancia, como lo tiene una mano que coge algo — caia por
    debajo del limite y se leia como un puno.

    Aqui la mano es LA MANO: la de `core/hand_geometry.py`, la misma
    que usan el clasificador, el filtro y los umbrales. Las pruebas y
    el producto miden contra lo mismo, y cuando el producto se mueve,
    las pruebas lo dicen.

EL RATON DE MENTIRA
    El raton de verdad habla con `user32.dll`, y una prueba que moviera
    el raton de verdad lo moveria mientras el desarrollador trabaja. El
    raton de mentira escribe lo que se le pidio, y la prueba es sobre
    la DECISION, no sobre el raton.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.hand_geometry import hand_points  # noqa: E402

# El ritmo de la camara: quince cuadros por segundo.
CUADRO = 1.0 / 15.0

# Los numeros de la mano canonica, medidos una vez y apuntados aqui
# para que las pruebas puedan decir si el producto se ha movido.
REACH_DE_LA_MANO_CANONICA = {
    "open": 0.89,
    "pinch": 0.55,
    "fist": 0.26,
}


class FakeDriver:
    """El raton que escribe lo que se le pidio."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.keys: set[int] = set()
        self._pos = (0, 0)

    def available(self) -> bool:
        return True

    def move(self, x, y):
        self._pos = (int(x), int(y))
        self.calls.append(("move", int(x), int(y)))
        return True

    def click(self):
        self.calls.append(("click",))
        return True

    def press(self):
        self.calls.append(("press",))
        return True

    def release(self):
        self.calls.append(("release",))
        return True

    def press_right(self):
        self.calls.append(("press_right",))
        return True

    def release_right(self):
        self.calls.append(("release_right",))
        return True

    def right_click(self):
        self.calls.append(("right",))
        return True

    def middle_click(self):
        self.calls.append(("middle_click",))
        return True

    def double_click(self):
        self.calls.append(("double_click",))
        return True

    def scroll(self, notches):
        self.calls.append(("scroll", int(notches)))
        return True

    def scroll_sideways(self, notches):
        self.calls.append(("side", int(notches)))
        return True

    def zoom(self, direction):
        self.calls.append(("zoom", int(direction)))
        return True

    def key(self, vk, down):
        self.calls.append(("key", int(vk), bool(down)))
        if down:
            self.keys.add(int(vk))
        else:
            self.keys.discard(int(vk))
        return True

    def tap(self, vk, alt=False):
        self.calls.append(("tap", int(vk), bool(alt)))
        return True

    # LA POSICION REAL DEL CURSOR. El servo la lee para saber si el
    # raton de verdad se ha movido por su cuenta.
    def cursor_pos(self):
        return self._pos

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]


def _span(points) -> float:
    """El tamano de la mano: de la muneca a la articulacion del corazon."""
    return math.hypot(points[9][0] - points[0][0], points[9][1] - points[0][1])


def mano(pose: str = "open",
         out: tuple[bool, bool, bool, bool] | None = None,
         closeness: float | None = None,
         gap: float = 0.0,
         dx: float = 0.0,
         dy: float = 0.0,
         spread: float = 0.0,
         aim: tuple[float, float] | None = None,
         handedness: str = "Right"):
    """Una mano de la forma que se le pide.

    `pose`    una de las posturas que la mano canonica sabe hacer:
              open, pinch, fist, point, two, three.

    `out`     cuales de los cuatro dedos estan fuera (indice, corazon,
              anular, menique), cuando la postura no lo dice de por si.
              Es como se escribe un pellizco con el corazon fuera: el
              shift.

    `closeness` la cercania que se quiere entre el pulgar y el indice,
              para poder hacer un pellizco que tiembla justo encima de
              la linea.

    `gap`     los milimetros de separacion que se anaden al pellizco.

    `spread`  cuanto se aleja el corazon del indice: el zoom.

    `dx`, `dy`  quanto se mueve la mano entera.

    `aim`     el punto al que apunta, si no es el que trae la mano.
    """
    from gui.gesture import Hand

    points = hand_points(pose, out, spread)
    if closeness is not None or gap:
        index = points[8]
        thumb = points[4]
        d = math.hypot(thumb[0] - index[0], thumb[1] - index[1])
        extra = gap
        if closeness is not None:
            extra = _span(points) * 0.85 * (1.0 - closeness) - d
        if extra > 0 and d > 0:
            ux = (thumb[0] - index[0]) / d
            uy = (thumb[1] - index[1]) / d
            points[4] = (thumb[0] + ux * extra, thumb[1] + uy * extra)
            points[3] = (points[3][0] + ux * extra * 0.5,
                         points[3][1] + uy * extra * 0.5)
    if dx or dy:
        points = [(x + dx, y + dy) for x, y in points]
    mano_real = Hand(points, handedness)
    mano_real.measure()
    if aim is not None:
        mano_real.aim_x, mano_real.aim_y = aim
    return mano_real


def servicio(driver: FakeDriver | None = None):
    """El servicio de gestos con el raton de mentira."""
    from gui.gesture import GestureService

    svc = GestureService()
    svc._driver = driver if driver is not None else FakeDriver()
    svc._drive_pointer = True
    return svc


def clase_de_puntero(svc) -> list[tuple]:
    """Los movimientos que ha hecho el puntero."""
    return [c for c in svc._driver.calls if c[0] == "move"]
