"use client";

import { type ReactNode, useId, useRef, useState } from "react";

/** Native modal owns focus trapping, Escape and an inert background. Draft stays mounted. */
export function CatalogDialog({
  title,
  children,
  feedback,
}: {
  title: string;
  children: ReactNode;
  feedback?: string;
}) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  return (
    <div className="admin-create">
      <button
        ref={trigger}
        className="button secondary"
        type="button"
        onClick={() => {
          setOpen(true);
          dialog.current?.showModal();
        }}
      >
        {title}
      </button>
      <dialog
        ref={dialog}
        className="catalog-dialog"
        aria-labelledby={id}
        onClose={() => {
          setOpen(false);
          trigger.current?.focus();
        }}
      >
        <div className="catalog-dialog-heading">
          <h3 id={id}>{title}</h3>
          <button
            className="button secondary"
            type="button"
            onClick={() => dialog.current?.close()}
          >
            Отмена / закрыть
          </button>
        </div>
        {open && feedback && (
          <p role="status" className="admin-alert">
            {feedback}
          </p>
        )}
        {children}
      </dialog>
    </div>
  );
}
