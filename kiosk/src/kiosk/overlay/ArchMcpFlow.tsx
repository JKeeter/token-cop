import { Globe, SquareTerminal, Workflow } from 'lucide-react';
import { useKiosk } from '../state';
import { ArchStage, type ArchEdge, type ArchNode } from './archparts';
import agentcoreIcon from './aws/AmazonBedrockAgentCore.svg';
import bedrockIcon from './aws/AmazonBedrock.svg';
import cloudwatchIcon from './aws/AmazonCloudWatch.svg';
import cognitoIcon from './aws/AmazonCognito.svg';
import costIcon from './aws/AWSCostExplorer.svg';
import lambdaIcon from './aws/AWSLambda.svg';
import s3Icon from './aws/AmazonS3.svg';
import userIcon from './aws/User.svg';

// Scene: how a /tokcop question travels from Claude Code to an audited answer.
const NODES: ArchNode[] = [
  { id: 'dev', x: 6, y: 32, title: 'Developer', sub: 'asks /tokcop', icon: userIcon, w: 120 },
  { id: 'claude-code', x: 19, y: 32, title: 'Claude Code', sub: 'MCP client + skill', glyph: <SquareTerminal className="h-9 w-9" /> },
  { id: 'mcp-server', x: 33, y: 32, title: 'MCP Server', sub: 'mcp_server.py · stdio', glyph: <Workflow className="h-9 w-9" /> },
  { id: 'cognito', x: 47, y: 32, title: 'Amazon Cognito', sub: 'OAuth2 · JWT', icon: cognitoIcon },
  { id: 'gateway', x: 61, y: 32, title: 'AgentCore Gateway', sub: 'token-cop-gateway', icon: agentcoreIcon },
  { id: 'lambda', x: 75, y: 32, title: 'AWS Lambda', sub: 'gateway target', icon: lambdaIcon },
  { id: 'runtime', x: 90, y: 32, title: 'AgentCore Runtime', sub: 'Strands agent', icon: agentcoreIcon },
  { id: 'bedrock', x: 90, y: 62, title: 'Amazon Bedrock', sub: 'Claude Sonnet', icon: bedrockIcon },
  { id: 'cloudwatch', x: 14, y: 84, title: 'CloudWatch', sub: 'Bedrock metrics', icon: cloudwatchIcon, w: 140 },
  { id: 'cost', x: 31, y: 84, title: 'Cost Explorer', sub: 'attribution', icon: costIcon, w: 140 },
  { id: 's3', x: 48, y: 84, title: 'Amazon S3', sub: 'invocation logs', icon: s3Icon, w: 140 },
  { id: 'apis', x: 65, y: 84, title: 'Provider APIs', sub: 'OpenRouter · OpenAI', glyph: <Globe className="h-9 w-9" />, w: 150 },
  { id: 'memory', x: 82, y: 84, title: 'AgentCore Memory', sub: 'usage snapshots', icon: agentcoreIcon, w: 150 },
];

const EDGES: ArchEdge[] = [
  { from: 'dev', to: 'claude-code', step: 0 },
  { from: 'claude-code', to: 'mcp-server', step: 1 },
  { from: 'mcp-server', to: 'cognito', step: 1 },
  { from: 'cognito', to: 'gateway', step: 2 },
  { from: 'gateway', to: 'lambda', step: 2 },
  { from: 'lambda', to: 'runtime', step: 3 },
  { from: 'runtime', to: 'bedrock', step: 3 },
  { from: 'runtime', to: 'cloudwatch', step: 4, curve: { cx: 55, cy: 62 } },
  { from: 'runtime', to: 'cost', step: 4, curve: { cx: 62, cy: 66 } },
  { from: 'runtime', to: 's3', step: 4, curve: { cx: 70, cy: 68 } },
  { from: 'runtime', to: 'apis', step: 4 },
  { from: 'runtime', to: 'memory', step: 4 },
  { from: 'runtime', to: 'claude-code', step: 5, dashed: true, curve: { cx: 54, cy: 2 } },
];

const HOT_BY_STEP: string[][] = [
  ['dev', 'claude-code'],
  ['mcp-server', 'cognito'],
  ['gateway', 'lambda'],
  ['runtime', 'bedrock'],
  ['cloudwatch', 'cost', 's3', 'apis', 'memory'],
  ['claude-code', 'dev'],
];

export default function ArchMcpFlow() {
  const step = useKiosk((s) => s.archStep);
  return (
    <ArchStage
      kicker="Architecture · the skill path"
      title="One question → an audited answer"
      nodes={NODES}
      edges={EDGES}
      hotByStep={HOT_BY_STEP}
      step={step}
    />
  );
}
