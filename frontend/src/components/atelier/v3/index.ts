// Atelier v0.3 component barrel.
// All v3 components export named symbols; consumers should import from
// `@/components/atelier/v3`. Updated by controller after each wave.

// Task 1 — flag + shared types
export { useAtelierVariant, resolveAtelierVariant, type AtelierVariant } from "./useAtelierVariant";
export { type MediaKind, type MediaNodeView, toMediaNodeView } from "./types";

// Wave A — leaf nodes
export { MediaNode } from "./MediaNode";
export { DraftNode } from "./DraftNode";
export { DraftWorkbench } from "./DraftWorkbench";
export { IdeaNode } from "./IdeaNode";
export { CommentNode } from "./CommentNode";

// Wave B — plan + chrome
export { PlanNode } from "./PlanNode";
export { SelectionActionBar } from "./SelectionActionBar";
export { BottomNavRail } from "./BottomNavRail";
export { Minimap } from "./Minimap";

// Wave C — top-level chrome + composer
export { ToolbarV3 } from "./ToolbarV3";
export { LeftRailV3, type LeftRailMode } from "./LeftRailV3";
export { RailPanel } from "./RailPanel";
export { RightRailV3, type AgentRailStatus, type PermissionMode } from "./RightRailV3";
export { AgentPanelV3 } from "./AgentPanelV3";
export {
  Composer,
  CapabilityIcon,
  ChipDropdown,
  composerPlacement,
  type ComposerTab,
  type ComposerSubmitPayload,
  type ComposerRef,
  type ComposerAnchor,
  type ComposerViewport,
  type ComposerSize,
  type ComposerPlacement,
} from "./Composer";

// Wave D — asset library
export { AssetLibrary, type AssetKind, type AssetCategory } from "./AssetLibrary";
