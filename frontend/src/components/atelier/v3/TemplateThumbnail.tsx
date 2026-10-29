"use client";

import type { WorkflowTemplate } from "./workflowTemplates";

export function TemplateThumbnail({ template }: { template: WorkflowTemplate }) {
  const nodes = template.nodes;
  const minX = Math.min(...nodes.map(node => node.x));
  const minY = Math.min(...nodes.map(node => node.y));
  const maxX = Math.max(...nodes.map(node => node.x + node.width));
  const maxY = Math.max(...nodes.map(node => node.y + node.height));
  const width = Math.max(1, maxX - minX);
  const height = Math.max(1, maxY - minY);
  const sx = (x: number) => 10 + ((x - minX) / width) * 116;
  const sy = (y: number) => 10 + ((y - minY) / height) * 62;
  return <svg aria-hidden="true" viewBox="0 0 136 82" className="h-[82px] w-full rounded-xl border border-white/[0.06] bg-black/25">
    <defs><linearGradient id={`template-${template.id}`} x1="0" y1="0" x2="1" y2="1"><stop stopColor="#646cff" stopOpacity="0.45" /><stop offset="1" stopColor="#ff0080" stopOpacity="0.24" /></linearGradient></defs>
    {template.edges.map(edge => {
      const source = nodes.find(node => node.localId === edge.source);
      const target = nodes.find(node => node.localId === edge.target);
      if (!source || !target) return null;
      return <line key={edge.id} x1={sx(source.x + source.width / 2)} y1={sy(source.y + source.height / 2)} x2={sx(target.x + target.width / 2)} y2={sy(target.y + target.height / 2)} stroke="rgba(255,255,255,0.26)" strokeDasharray="4 4" />;
    })}
    {nodes.map(node => <rect key={node.localId} x={sx(node.x)} y={sy(node.y)} width={Math.max(16, (node.width / width) * 44)} height={Math.max(10, (node.height / height) * 24)} rx="5" fill={`url(#template-${template.id})`} stroke="rgba(255,255,255,0.22)" />)}
  </svg>;
}
