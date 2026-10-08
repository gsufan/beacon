import ReactMarkdown from "react-markdown";

export default function MarkdownViewer({ content }: { content: string }) {
  return (
    <div className="prose prose-neutral dark:prose-invert prose-sm max-w-none prose-code:rounded prose-code:bg-surface-2 prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:font-normal prose-code:before:content-none prose-code:after:content-none prose-pre:bg-code-bg prose-pre:text-code-ink">
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  );
}
