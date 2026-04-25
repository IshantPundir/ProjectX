"use client";

import type { ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./Dialog";

export interface DangerConfirmDialogProps {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  pending?: boolean;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}

/**
 * Destructive-confirmation dialog. Stays open while `pending` is true so
 * the consumer can show in-flight state and keep the dialog open on
 * mutation error. Parent must explicitly call `onClose()` after a
 * successful mutation.
 *
 * Used for every "are you sure?" destructive action in the app.
 */
export function DangerConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pendingLabel,
  pending = false,
  onConfirm,
  onClose,
}: DangerConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-btn ghost sm"
            disabled={pending}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              void onConfirm();
            }}
            disabled={pending}
            className="px-btn danger sm"
          >
            {pending ? (pendingLabel ?? `${confirmLabel}…`) : confirmLabel}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
