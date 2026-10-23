import React, { useId, useMemo } from "react";
import { SearchIcon } from "lucide-react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { Button as StudioButton } from "./components/ui/button";
import { Badge } from "./components/ui/badge";
import { Checkbox as StudioCheckbox } from "./components/ui/checkbox";
import { Input } from "./components/ui/input";
import {
  Select as SelectRoot,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./components/ui/select";
import { Tabs as TabsRoot, TabsList, TabsTrigger } from "./components/ui/tabs";
import { RadioGroup, RadioGroupItem } from "./components/ui/radio-group";
import { Textarea as StudioTextarea } from "./components/ui/textarea";
import {
  Table,
  TableHeader,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
} from "./components/ui/table";
import {
  Tooltip as TooltipRoot,
  TooltipContent,
  TooltipTrigger,
  TooltipProvider,
} from "./components/ui/tooltip";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "./components/ui/sheet";

export function KitProvider({ children }) {
  return <TooltipProvider delayDuration={250}>{children}</TooltipProvider>;
}

export function Button({
  primary,
  quiet,
  icon,
  children,
  className = "",
  ...props
}) {
  return (
    <StudioButton
      variant={primary ? "default" : quiet ? "ghost" : "outline"}
      className={"text-[13px] " + className}
      {...props}
    >
      {icon}
      {children}
    </StudioButton>
  );
}

export function IconButton({ label, children, className = "", ...props }) {
  return (
    <StudioButton
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      className={"text-muted-foreground " + className}
      {...props}
    >
      {children}
    </StudioButton>
  );
}

// Adapted from Studio input-14: native input with a leading icon.
export function Search({ value, onChange, placeholder, label }) {
  return (
    <div className="relative kit-input">
      <div className="text-muted-foreground pointer-events-none absolute inset-y-0 left-0 flex items-center justify-center pl-3">
        <SearchIcon className="size-4" />
      </div>
      <Input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label}
        className="peer h-9 bg-background pl-9 text-[13px] md:text-[13px]"
      />
    </div>
  );
}

export function Select({ options, value, onChange, label }) {
  return (
    <SelectRoot value={value} onValueChange={onChange}>
      <SelectTrigger
        aria-label={label}
        className="kit-select h-9 bg-background text-[12px]"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem
            key={option.value}
            value={option.value}
            className="text-[13px]"
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </SelectRoot>
  );
}

export function Checkbox({ checked, onChange, label, indeterminate = false }) {
  return (
    <StudioCheckbox
      checked={indeterminate ? "indeterminate" : checked}
      onCheckedChange={(value) => onChange(value === true)}
      aria-label={label}
    />
  );
}

export function Tag({ color = "neutral", children }) {
  const styles = {
    neutral: "bg-muted text-muted-foreground",
    warning:
      "bg-amber-600/10 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300",
    success:
      "bg-emerald-600/10 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300",
    danger: "bg-destructive/10 text-destructive",
    brand:
      "bg-indigo-600/10 text-indigo-700 dark:bg-indigo-400/10 dark:text-indigo-300",
  };
  return (
    <Badge
      variant="secondary"
      className={
        "rounded-md border-0 px-2 py-0.5 text-[10px] font-medium " +
        styles[color]
      }
    >
      {children}
    </Badge>
  );
}

// Studio tabs-11 underline variant, with controlled values and accessible panels.
export function Tabs({ value, onChange, items, label }) {
  return (
    <TabsRoot value={value} onValueChange={onChange} className="kit-tabs">
      <TabsList
        aria-label={label}
        className="h-10 w-full justify-start rounded-none border-b bg-background p-0"
      >
        {items.map((item) => (
          <TabsTrigger
            key={item.value}
            value={item.value}
            id={"tab-" + item.value}
            aria-controls={"pane-" + item.value}
            className="h-full flex-none gap-2 rounded-none border-0 border-b-2 border-transparent bg-background px-3 text-[12px] data-[state=active]:border-primary data-[state=active]:shadow-none dark:data-[state=active]:border-primary dark:data-[state=active]:bg-transparent"
          >
            {item.icon}
            {item.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </TabsRoot>
  );
}

// Studio radio-group-08 chip variant, adapted to PASS / FAIL / DEFER.
export function Verdict({ value, onChange, label }) {
  const id = useId();
  const items = [
    { value: "PASS", label: "PASS" },
    { value: "FAIL", label: "FAIL" },
    { value: "DEFER", label: "보류" },
  ];
  return (
    <RadioGroup
      value={value}
      onValueChange={onChange}
      aria-label={label}
      className="verdict-control grid grid-cols-3 gap-2"
    >
      {items.map((item) => (
        <label
          key={item.value}
          className="border-input has-data-[state=checked]:border-primary/80 has-data-[state=checked]:bg-accent has-focus-visible:border-ring has-focus-visible:ring-ring/50 relative flex cursor-pointer flex-col items-center rounded-md border px-2 py-2.5 text-center shadow-xs transition-[color,box-shadow] outline-none has-focus-visible:ring-[3px]"
        >
          <RadioGroupItem
            id={id + "-" + item.value}
            value={item.value}
            className="sr-only after:absolute after:inset-0"
            aria-label={item.label}
          />
          <span className="text-foreground text-[12px] leading-none font-medium">
            {item.label}
          </span>
        </label>
      ))}
    </RadioGroup>
  );
}

export function Textarea({ value, onChange, label }) {
  return (
    <StudioTextarea
      value={value}
      onChange={(event) => onChange(event.target.value)}
      rows={3}
      aria-label={label}
      className="kit-textarea resize-y bg-background text-[13px] leading-relaxed"
    />
  );
}

export function Tooltip({ label, children }) {
  return (
    <TooltipRoot>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </TooltipRoot>
  );
}

// Adapts Studio data-table-04's TanStack table and row-selection pattern.
// Filtering and pagination operate over the full selected group in App.
export function LogTable({ rows, selectedId, onSelect, checked, onCheck }) {
  const allChecked =
    rows.length > 0 && rows.every((record) => checked.has(record.id));
  const someChecked = rows.some((record) => checked.has(record.id));
  const columns = useMemo(
    () => [
      {
        id: "selection",
        header: () => (
          <Checkbox
            checked={allChecked}
            indeterminate={someChecked && !allChecked}
            onChange={(value) =>
              rows.forEach((record) => onCheck(record.id, value))
            }
            label="현재 페이지 전체 선택"
          />
        ),
        cell: ({ row }) => (
          <span
            className="inline-flex"
            onClick={(event) => event.stopPropagation()}
          >
            <Checkbox
              checked={checked.has(row.original.id)}
              onChange={(value) => onCheck(row.original.id, value)}
              label={row.original.id + " 선택"}
            />
          </span>
        ),
      },
      {
        accessorKey: "line",
        header: "원문 행",
        cell: ({ row }) => <span className="line-no">{row.original.line}</span>,
      },
      {
        accessorKey: "target",
        header: "대상 경로",
        cell: ({ row }) => (
          <span className="target-path">{row.original.target}</span>
        ),
      },
      {
        accessorKey: "mode",
        header: "실행 모드",
        cell: ({ row }) => (
          <span className="mode-text">{row.original.mode}</span>
        ),
      },
    ],
    [rows, checked, onCheck, allChecked, someChecked],
  );
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <Table className="actual-table">
      <TableHeader>
        {table.getHeaderGroups().map((headerGroup) => (
          <TableRow key={headerGroup.id}>
            {headerGroup.headers.map((header) => (
              <TableHead
                key={header.id}
                className={header.id === "selection" ? "w-10 pl-4" : ""}
              >
                {header.isPlaceholder
                  ? null
                  : flexRender(
                      header.column.columnDef.header,
                      header.getContext(),
                    )}
              </TableHead>
            ))}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.map((row) => (
          <TableRow
            key={row.original.id}
            className={row.original.id === selectedId ? "selected-log" : ""}
            tabIndex={0}
            aria-selected={row.original.id === selectedId}
            aria-label={row.original.id + " 원문 보기"}
            onClick={() => onSelect(row.original.id)}
            onKeyDown={(event) => {
              if (
                event.target === event.currentTarget &&
                (event.key === "Enter" || event.key === " ")
              ) {
                event.preventDefault();
                onSelect(row.original.id);
              }
            }}
          >
            {row.getVisibleCells().map((cell) => (
              <TableCell
                key={cell.id}
                className={cell.column.id === "selection" ? "pl-4" : ""}
              >
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function Drawer({ opened, onClose, title, children, returnFocusRef }) {
  return (
    <Sheet open={opened} onOpenChange={(value) => !value && onClose()}>
      <SheetContent
        onCloseAutoFocus={(event) => {
          if (returnFocusRef?.current) {
            event.preventDefault();
            returnFocusRef.current.focus({ preventScroll: true });
          }
        }}
        side="right"
        className="w-full gap-0 sm:max-w-[430px]"
      >
        <SheetHeader className="border-b border-border px-6 pt-7 pb-5">
          <SheetTitle className="text-base">{title}</SheetTitle>
          <SheetDescription>현재 검토 시안의 합성 자료입니다.</SheetDescription>
        </SheetHeader>
        <div className="overflow-y-auto px-6 py-5">{children}</div>
      </SheetContent>
    </Sheet>
  );
}
