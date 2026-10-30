import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { SortableItem } from "./sortable-item";

const dragKey = vi.hoisted(() => vi.fn());
vi.mock("@dnd-kit/sortable", () => ({
  useSortable: () => ({
    attributes: {
      "aria-describedby": "drag-instructions",
      "aria-pressed": false,
      role: "button",
      tabIndex: 0,
    },
    isDragging: false,
    listeners: { onKeyDown: dragKey },
    setNodeRef: vi.fn(),
    transform: null,
    transition: undefined,
  }),
}));
vi.mock("./use-drag-over-store", () => ({ useDropEdgeForRow: () => null }));

it.each(["item", "subitem"] as const)(
  "exposes %s children separately while retaining keyboard drag activation",
  (variant) => {
    const host = document.createElement("ul");
    document.body.append(host);
    const root = createRoot(host);
    dragKey.mockClear();
    try {
      flushSync(() =>
        root.render(
          <SortableItem id="fixture" variant={variant}>
            <button type="button">Open fixture</button>
            <button aria-label="More actions for fixture" type="button">
              Actions
            </button>
          </SortableItem>,
        ),
      );
      const group = host.querySelector<HTMLElement>('[role="group"]');
      if (!group) throw new Error("Sortable group missing");
      expect(group).not.toHaveAttribute("aria-pressed");
      expect(group).toHaveAttribute("aria-describedby", "drag-instructions");
      expect(group.querySelectorAll("button")).toHaveLength(2);
      group.focus();
      expect(document.activeElement).toBe(group);
      flushSync(() =>
        group.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true })),
      );
      expect(dragKey).toHaveBeenCalledOnce();
    } finally {
      flushSync(() => root.unmount());
      host.remove();
    }
  },
);
