import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CodeWhiteboard } from "./CodeWhiteboard";

vi.mock("@uiw/react-codemirror", () => ({
  default: ({ value, onChange, ...props }: { value: string; onChange: (value: string) => void; [key: string]: unknown }) => (
    <textarea
      aria-label={String(props["aria-label"])}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

describe("CodeWhiteboard", () => {
  it("supports language selection, clearing, and bounded text attachment", () => {
    const onAttach = vi.fn();
    render(<CodeWhiteboard onAttach={onAttach} />);

    fireEvent.click(screen.getByRole("button", { name: "代码白板" }));
    fireEvent.change(screen.getByLabelText("语言"), { target: { value: "typescript" } });
    fireEvent.change(screen.getByLabelText("代码编辑器"), {
      target: { value: "const answer: number = 42;" },
    });
    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    expect(screen.getByLabelText("代码编辑器")).toHaveValue("");

    fireEvent.change(screen.getByLabelText("代码编辑器"), {
      target: { value: "const answer: number = 42;" },
    });
    fireEvent.click(screen.getByRole("button", { name: "附加到回答" }));

    expect(onAttach).toHaveBeenCalledWith({
      attachment_type: "code",
      language: "typescript",
      content: "const answer: number = 42;",
      filename: "solution.ts",
    });
  });
});
