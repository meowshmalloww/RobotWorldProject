import { useState } from "react";
import { Icon, type IconName } from "./Icon";

export interface TreeNodeData {
  id: string;
  name: string;
  icon?: IconName;
  tag?: string;
  locked?: boolean;
  defaultHidden?: boolean;
  children?: TreeNodeData[];
}

export function Tree({
  nodes,
  selected,
  onSelect,
  depth = 0,
}: {
  nodes: TreeNodeData[];
  selected?: string | null;
  onSelect?: (id: string, name: string) => void;
  depth?: number;
}) {
  return (
    <div className="tree" role={depth === 0 ? "tree" : "group"}>
      {nodes.map((n) => (
        <TreeRow key={n.id} node={n} depth={depth} selected={selected} onSelect={onSelect} />
      ))}
    </div>
  );
}

function TreeRow({
  node, depth, selected, onSelect,
}: {
  node: TreeNodeData; depth: number; selected?: string | null; onSelect?: (id: string, name: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const [visible, setVisible] = useState(!node.defaultHidden);
  const hasKids = !!node.children?.length;
  return (
    <>
      <div
        className={`tree-row ${selected === node.id ? "selected" : ""}`}
        style={{ paddingLeft: 8 + depth * 14 }}
        onClick={() => onSelect?.(node.id, node.name)}
        role="treeitem"
        aria-expanded={hasKids ? open : undefined}
      >
        <span
          className={`caret ${hasKids ? (open ? "open" : "") : "leaf"}`}
          onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        >
          <Icon name="chevronRight" size={10} />
        </span>
        <span className="t-ico"><Icon name={node.icon ?? "box"} size={13} /></span>
        <span className="t-label">{node.name}</span>
        {node.tag && <span className="t-tag">{node.tag}</span>}
        {node.locked && <Icon name="lock" size={11} style={{ color: "var(--text-3)", flex: "none" }} />}
        <span
          className={`t-vis ${visible ? "" : "off"}`}
          onClick={(e) => { e.stopPropagation(); setVisible(!visible); }}
          title={visible ? "Hide" : "Show"}
        >
          <Icon name={visible ? "eye" : "eyeOff"} size={12} />
        </span>
      </div>
      {hasKids && open && (
        <Tree nodes={node.children!} depth={depth + 1} selected={selected} onSelect={onSelect} />
      )}
    </>
  );
}
