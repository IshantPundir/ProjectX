"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Select — native <select> styled with .px-input.
 *
 * Exposes a shadcn-compatible compound API (Select/SelectTrigger/SelectValue/
 * SelectContent/SelectItem). SelectTrigger, SelectValue and SelectContent are
 * structural markers whose children/props are walked and flattened into a
 * single native <select>. The design bundle itself uses native <select> with
 * .px-input, so this is pixel-perfect to the prototype.
 */

export interface SelectProps {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  required?: boolean;
  name?: string;
  id?: string;
  children: React.ReactNode;
}

interface SelectTriggerProps extends React.HTMLAttributes<HTMLDivElement> {
  id?: string;
  children: React.ReactNode;
  size?: "default" | "sm";
}

interface SelectValueProps {
  placeholder?: string;
  children?: React.ReactNode;
}

interface SelectContentProps {
  children: React.ReactNode;
}

export interface SelectItemProps {
  value: string;
  disabled?: boolean;
  children: React.ReactNode;
}

type FlatItem = { value: string; label: string; disabled?: boolean };

/**
 * Recursively extract plain text from a React node. Native <option> can only
 * contain text (see HTML spec §4.10.10), so if a caller passes rich children
 * like <span>Title<span>subtitle</span></span> we flatten them to a string
 * instead of inserting a <span> child and crashing hydration. Joins with a
 * single space between sibling text runs.
 */
function nodeToText(node: React.ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) {
    return node.map(nodeToText).filter(Boolean).join(" ");
  }
  if (React.isValidElement(node)) {
    const child = (node.props as { children?: React.ReactNode } | undefined)
      ?.children;
    return nodeToText(child);
  }
  return "";
}

function extractItems(node: React.ReactNode): FlatItem[] {
  const items: FlatItem[] = [];
  React.Children.forEach(node, (child) => {
    if (!React.isValidElement(child)) return;
    if (child.type === SelectItem) {
      const props = child.props as SelectItemProps;
      items.push({
        value: props.value,
        label: nodeToText(props.children) || props.value,
        disabled: props.disabled,
      });
      return;
    }
    if (
      (child.type === SelectContent || child.type === SelectGroup) &&
      child.props &&
      typeof child.props === "object" &&
      "children" in child.props
    ) {
      items.push(
        ...extractItems((child.props as { children?: React.ReactNode }).children),
      );
    }
  });
  return items;
}

function extractTriggerInfo(
  node: React.ReactNode,
): { id?: string; placeholder?: string; size?: "default" | "sm"; className?: string } {
  let info: {
    id?: string;
    placeholder?: string;
    size?: "default" | "sm";
    className?: string;
  } = {};
  React.Children.forEach(node, (child) => {
    if (!React.isValidElement(child)) return;
    if (child.type === SelectTrigger) {
      const props = child.props as SelectTriggerProps;
      info = {
        id: props.id,
        size: props.size,
        className: props.className,
      };
      React.Children.forEach(props.children, (cc) => {
        if (
          React.isValidElement(cc) &&
          cc.type === SelectValue &&
          cc.props &&
          typeof cc.props === "object" &&
          "placeholder" in cc.props
        ) {
          info.placeholder = (cc.props as SelectValueProps).placeholder;
        }
      });
    }
  });
  return info;
}

export function Select({
  value,
  defaultValue,
  onValueChange,
  disabled,
  required,
  name,
  id,
  children,
}: SelectProps) {
  const items = extractItems(children);
  const triggerInfo = extractTriggerInfo(children);

  const resolvedId = id ?? triggerInfo.id;
  const sizeClass =
    triggerInfo.size === "sm" ? "sm" : "";
  const triggerClass = triggerInfo.className;

  // Controlled vs uncontrolled: native <select> requires value OR defaultValue
  // exclusively. If a caller passes neither we start uncontrolled at "".
  const isControlled = value !== undefined;
  const selectProps: React.SelectHTMLAttributes<HTMLSelectElement> = {
    name,
    id: resolvedId,
    required,
    disabled,
    onChange: (e) => onValueChange?.(e.target.value),
  };
  if (isControlled) selectProps.value = value;
  else selectProps.defaultValue = defaultValue ?? "";

  return (
    <select
      className={cn("px-input", sizeClass, triggerClass)}
      {...selectProps}
    >
      {triggerInfo.placeholder !== undefined && (
        <option value="" disabled>
          {triggerInfo.placeholder}
        </option>
      )}
      {items.map((it) => (
        <option key={it.value} value={it.value} disabled={it.disabled}>
          {it.label}
        </option>
      ))}
    </select>
  );
}

/** Structural marker — children are walked by <Select>. Not rendered directly. */
export function SelectTrigger(_: SelectTriggerProps) {
  return null;
}

/** Structural marker — `placeholder` read by <Select>. */
export function SelectValue(_: SelectValueProps) {
  return null;
}

/** Structural marker — children flattened to <option>s. */
export function SelectContent(_: SelectContentProps) {
  return null;
}

/** Optional grouping marker — children flattened, no <optgroup> rendered. */
export function SelectGroup({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

/** Leaf item rendered as <option> inside the native <select>. */
export function SelectItem(_: SelectItemProps) {
  return null;
}
