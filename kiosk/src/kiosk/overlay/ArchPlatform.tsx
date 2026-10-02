import { Scale } from 'lucide-react';
import { useKiosk } from '../state';
import { ArchStage, type ArchEdge, type ArchNode } from './archparts';
import agentcoreIcon from './aws/AmazonBedrockAgentCore.svg';
import cloudwatchIcon from './aws/AmazonCloudWatch.svg';
import cognitoIcon from './aws/AmazonCognito.svg';
import dynamodbIcon from './aws/AmazonDynamoDB.svg';
import iamIcon from './aws/AWSIAM.svg';
import lambdaIcon from './aws/AWSLambda.svg';
import xrayIcon from './aws/AWSXRay.svg';

// Scene: the AgentCore platform around the agent, plus the hard budget-
// enforcement loop (CloudWatch Logs → meter Lambda → DynamoDB → IAM deny).
const NODES: ArchNode[] = [
  { id: 'runtime', x: 50, y: 34, title: 'AgentCore Runtime', sub: 'Strands agent · Claude Sonnet', icon: agentcoreIcon, w: 200 },
  { id: 'memory', x: 20, y: 14, title: 'AgentCore Memory', sub: 'usage snapshots', icon: agentcoreIcon },
  { id: 'gateway', x: 20, y: 48, title: 'AgentCore Gateway', sub: 'MCP tools · JWT', icon: agentcoreIcon },
  { id: 'identity', x: 80, y: 14, title: 'Identity', sub: 'Amazon Cognito OAuth', icon: cognitoIcon },
  { id: 'policy', x: 80, y: 48, title: 'Policy', sub: 'Cedar · LOG_ONLY → ENFORCE', glyph: <Scale className="h-9 w-9" /> },
  { id: 'obs', x: 50, y: 8, title: 'Observability', sub: 'OTEL → X-Ray · CloudWatch', icon: xrayIcon, w: 190 },
  { id: 'harness', x: 50, y: 62, title: 'Harness Twin', sub: 'same agent, pure config', icon: agentcoreIcon, w: 180 },
  { id: 'cwlogs', x: 14, y: 90, title: 'CloudWatch Logs', sub: 'invocation events', icon: cloudwatchIcon, w: 150 },
  { id: 'meter', x: 38, y: 90, title: 'Meter Lambda', sub: 'burn metering', icon: lambdaIcon, w: 150 },
  { id: 'ddb', x: 62, y: 90, title: 'DynamoDB', sub: 'spend per principal', icon: dynamodbIcon, w: 150 },
  { id: 'iam', x: 86, y: 90, title: 'IAM Deny Policy', sub: 'hard budget cap', icon: iamIcon, w: 150 },
];

const EDGES: ArchEdge[] = [
  { from: 'runtime', to: 'memory', step: 0 },
  { from: 'runtime', to: 'gateway', step: 0 },
  { from: 'runtime', to: 'identity', step: 1 },
  { from: 'runtime', to: 'policy', step: 1 },
  { from: 'runtime', to: 'obs', step: 2 },
  { from: 'runtime', to: 'harness', step: 3 },
  { from: 'cwlogs', to: 'meter', step: 4 },
  { from: 'meter', to: 'ddb', step: 4 },
  { from: 'ddb', to: 'iam', step: 4 },
  { from: 'iam', to: 'runtime', step: 4, dashed: true, curve: { cx: 96, cy: 60 } },
];

const HOT_BY_STEP: string[][] = [
  ['runtime', 'memory', 'gateway'],
  ['identity', 'policy'],
  ['obs'],
  ['harness'],
  ['cwlogs', 'meter', 'ddb', 'iam'],
];

export default function ArchPlatform() {
  const step = useKiosk((s) => s.archStep);
  return (
    <ArchStage
      kicker="Architecture · the platform"
      title="Governed, traced, enforced — on AgentCore"
      nodes={NODES}
      edges={EDGES}
      hotByStep={HOT_BY_STEP}
      step={step}
    />
  );
}
