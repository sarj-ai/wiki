import type { DocTab } from "@/types";

export function TabDivider({
  title,
  id,
  depth = 0,
}: {
  title: string;
  id: string;
  depth?: number;
}) {
  return (
    <div
      id={id}
      className={`doc-tab-divider ${depth > 0 ? "doc-tab-divider-sub" : ""}`}
      data-tab-divider
    >
      <div className="doc-tab-divider-line" />
      <span className="doc-tab-divider-label">{title}</span>
      <div className="doc-tab-divider-line" />
    </div>
  );
}

export function TabScrollSpy({
  tabs,
  activeIndex,
  onClickTab,
}: {
  tabs: DocTab[];
  activeIndex: number;
  onClickTab: (id: string) => void;
}) {
  const navItems = tabs.map((tab, i) => {
    const depth = tab.depth ?? 0;
    return (
      <button
        key={i}
        type="button"
        onClick={() => onClickTab(`doc-tab-${i}`)}
        className={`doc-tab-nav-item ${i === activeIndex ? "doc-tab-nav-active" : ""}`}
        style={
          typeof depth === "number" && depth > 0
            ? { paddingLeft: `${0.75 + depth * 1}rem` }
            : undefined
        }
      >
        {tab.title}
      </button>
    );
  });

  return (
    <div className="doc-tab-nav-anchor">
      {/* Desktop: ticks + card on hover */}
      <div className="doc-tab-nav-hover-zone">
        <div className="doc-tab-nav-ticks">
          {tabs.map((tab, i) => (
            <span
              key={i}
              className={`doc-tab-nav-tick ${i === activeIndex ? "doc-tab-nav-tick-active" : ""}`}
              data-depth={tab.depth ?? 0}
            />
          ))}
        </div>
        <nav className="doc-tab-nav-card">{navItems}</nav>
      </div>

      {/* Mobile: always visible horizontal bar */}
      <nav className="doc-tab-nav-mobile">{navItems}</nav>
    </div>
  );
}
