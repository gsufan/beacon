import ReactMarkdown from "react-markdown";

export default function MarkdownViewer({ content }: { content: string }) {
  return (
    <div className="prose prose-neutral dark:prose-invert prose-sm max-w-none">
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  );
}
