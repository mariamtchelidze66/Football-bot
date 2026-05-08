import { useState, useEffect } from "react";
import { MessageSquare, Zap, Trophy, BellRing, Search, Terminal, ArrowRight, Activity, Database, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

const LEAGUES = [
  "Premier League",
  "La Liga",
  "Serie A",
  "Bundesliga",
  "Ligue 1",
  "Champions League",
  "Europa League",
  "MLS",
  "Brazilian Serie A",
  "Argentine Primera División"
];

function HeroSection() {
  return (
    <section className="relative min-h-[90vh] flex items-center justify-center overflow-hidden border-b border-border">
      {/* Background Image */}
      <div className="absolute inset-0 z-0">
        <img 
          src="/stadium-hero.png" 
          alt="Stadium at night" 
          className="w-full h-full object-cover opacity-30 mix-blend-luminosity"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-background/80 via-background/60 to-background"></div>
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(0,255,100,0.1)_0%,rgba(0,0,0,0)_60%)]"></div>
      </div>

      <div className="relative z-10 container mx-auto px-6 text-center">
        <div className="inline-flex items-center gap-2 px-3 py-1 mb-8 border border-primary/30 bg-primary/10 text-primary uppercase text-xs tracking-widest backdrop-blur-sm animate-in fade-in slide-in-from-bottom-4 duration-700">
          <span className="w-2 h-2 bg-primary rounded-full animate-pulse"></span>
          Live Intelligence. Zero Delay.
        </div>
        
        <h1 className="text-5xl md:text-7xl lg:text-8xl font-bold tracking-tighter mb-6 uppercase leading-tight animate-in fade-in slide-in-from-bottom-8 duration-1000 delay-150 fill-mode-both">
          The Ultimate <br/>
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-primary to-blue-500">
            Football AI
          </span>
        </h1>
        
        <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto mb-10 tracking-wide leading-relaxed animate-in fade-in slide-in-from-bottom-8 duration-1000 delay-300 fill-mode-both">
          Always watching. Always searching. Get real-time news, live scores, and deep tactical analysis powered by Claude—directly in your Telegram.
        </p>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-4 animate-in fade-in slide-in-from-bottom-8 duration-1000 delay-500 fill-mode-both">
          <Button size="lg" className="h-14 px-8 text-lg font-bold uppercase tracking-wider bg-primary text-primary-foreground hover:bg-primary/90 hover:scale-105 transition-all shadow-[0_0_30px_rgba(0,255,100,0.3)]">
            Open in Telegram
            <ArrowRight className="ml-2 h-5 w-5" />
          </Button>
          <Button size="lg" variant="outline" className="h-14 px-8 text-lg uppercase tracking-wider border-muted-foreground/30 hover:bg-secondary">
            View Commands
          </Button>
        </div>
      </div>
    </section>
  );
}

function FeaturesSection() {
  const features = [
    {
      icon: <Search className="w-6 h-6 text-primary" />,
      title: "Web-Connected News",
      description: "Claude continuously scours the web for the latest transfer rumors, injury reports, and breaking news."
    },
    {
      icon: <Activity className="w-6 h-6 text-primary" />,
      title: "Live Scores",
      description: "Automatic score updates every 30 minutes for ongoing matches. Never miss a goal."
    },
    {
      icon: <Database className="w-6 h-6 text-primary" />,
      title: "Deep Knowledge",
      description: "Ask anything. Historical stats, tactical breakdowns, player comparisons—Claude knows it all."
    }
  ];

  return (
    <section className="py-24 bg-card relative overflow-hidden">
      <div className="container mx-auto px-6 relative z-10">
        <div className="grid md:grid-cols-3 gap-8">
          {features.map((feature, i) => (
            <div key={i} className="p-8 border border-border bg-background/50 hover:bg-background/80 transition-colors group">
              <div className="mb-6 p-4 bg-secondary inline-block rounded-none border border-primary/20 group-hover:border-primary/50 transition-colors">
                {feature.icon}
              </div>
              <h3 className="text-xl font-bold mb-4 uppercase tracking-wide">{feature.title}</h3>
              <p className="text-muted-foreground leading-relaxed text-sm">
                {feature.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function LeagueSection() {
  return (
    <section className="py-24 border-y border-border overflow-hidden bg-background">
      <div className="container mx-auto px-6 text-center mb-16">
        <h2 className="text-3xl md:text-4xl font-bold uppercase tracking-tight mb-4">Global Coverage</h2>
        <p className="text-muted-foreground max-w-xl mx-auto">
          Subscribe to specific leagues for targeted push notifications. We cover the world's most elite competitions.
        </p>
      </div>

      {/* Marquee effect */}
      <div className="relative w-full overflow-hidden flex bg-secondary/50 py-8 border-y border-border">
        <div className="flex whitespace-nowrap animate-[marquee_20s_linear_infinite]">
          {LEAGUES.concat(LEAGUES).map((league, i) => (
            <div key={i} className="mx-8 flex items-center gap-3">
              <Trophy className="w-5 h-5 text-primary opacity-70" />
              <span className="text-xl font-bold uppercase tracking-widest text-foreground/80">{league}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function AIAnalysisSection() {
  return (
    <section className="py-24 relative">
      <div className="container mx-auto px-6">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          <div className="order-2 lg:order-1 relative">
            <div className="absolute inset-0 bg-primary/20 blur-[100px]"></div>
            <img 
              src="/tactics-hologram.png" 
              alt="AI Tactical Analysis" 
              className="relative z-10 w-full rounded-none border border-primary/20"
            />
          </div>
          
          <div className="order-1 lg:order-2">
            <div className="inline-flex items-center gap-2 text-primary font-bold uppercase tracking-widest mb-6">
              <Zap className="w-4 h-4" />
              Powered by Claude 3.5
            </div>
            <h2 className="text-4xl md:text-5xl font-bold uppercase tracking-tight mb-6 leading-tight">
              Tactical Intelligence On Demand
            </h2>
            <p className="text-lg text-muted-foreground mb-8 leading-relaxed">
              Don't just watch the game. Understand it. Ask complex questions about team formations, xG statistics, manager strategies, or historical head-to-head records.
            </p>
            
            <div className="space-y-4">
              {[
                "Explain Guardiola's inverted fullback system",
                "Compare Messi and Ronaldo's 2012 stats",
                "What is the current injury list for Arsenal?"
              ].map((query, i) => (
                <div key={i} className="flex items-center gap-4 p-4 border border-border bg-card">
                  <MessageSquare className="w-5 h-5 text-primary shrink-0" />
                  <span className="font-mono text-sm text-foreground/90">"{query}"</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function CommandsSection() {
  const commands = [
    { cmd: "/start", desc: "Initialize your personal assistant" },
    { cmd: "/leagues", desc: "View and manage league subscriptions" },
    { cmd: "/news", desc: "Get top headlines from the last 24h" },
    { cmd: "/scores", desc: "Fetch live scores for active matches" },
    { cmd: "/clear", desc: "Reset conversation context" }
  ];

  return (
    <section className="py-24 bg-card border-t border-border">
      <div className="container mx-auto px-6 max-w-4xl">
        <div className="text-center mb-16">
          <Terminal className="w-12 h-12 text-primary mx-auto mb-6" />
          <h2 className="text-3xl md:text-4xl font-bold uppercase tracking-tight mb-4">Command Center</h2>
          <p className="text-muted-foreground">
            Simple Telegram slash commands to control the flow of information.
          </p>
        </div>

        <div className="grid gap-4">
          {commands.map((c, i) => (
            <div key={i} className="flex flex-col sm:flex-row sm:items-center justify-between p-6 border border-border bg-background hover:border-primary/50 transition-colors">
              <div className="text-xl font-mono text-primary font-bold mb-2 sm:mb-0">{c.cmd}</div>
              <div className="text-muted-foreground text-sm uppercase tracking-wide">{c.desc}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function CTASection() {
  return (
    <section className="py-32 relative overflow-hidden">
      <div className="absolute inset-0 bg-primary/5"></div>
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-primary/20 rounded-full blur-[120px] pointer-events-none"></div>
      
      <div className="container mx-auto px-6 text-center relative z-10">
        <h2 className="text-5xl md:text-7xl font-bold uppercase tracking-tighter mb-8">
          Enter The <br className="md:hidden" />
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-primary to-blue-500">
            Next Era
          </span>
        </h2>
        <p className="text-xl text-muted-foreground max-w-2xl mx-auto mb-12">
          Stop scrolling through ad-filled websites. Get the football information you care about, instantly, directly in Telegram.
        </p>
        
        <Button size="lg" className="h-16 px-10 text-xl font-bold uppercase tracking-wider bg-primary text-primary-foreground hover:bg-primary/90 shadow-[0_0_40px_rgba(0,255,100,0.4)] hover:shadow-[0_0_60px_rgba(0,255,100,0.6)] transition-all">
          Start Chatting Now
          <ArrowRight className="ml-2 h-6 w-6" />
        </Button>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="py-8 border-t border-border bg-background text-center text-sm text-muted-foreground uppercase tracking-widest">
      <p>Football Claude Bot. Powered by Anthropic & Replit.</p>
    </footer>
  );
}

export default function App() {
  return (
    <div className="min-h-screen bg-background text-foreground selection:bg-primary selection:text-primary-foreground font-sans">
      <style dangerouslySetInnerHTML={{__html: `
        @keyframes marquee {
          0% { transform: translateX(0%); }
          100% { transform: translateX(-50%); }
        }
      `}} />
      <HeroSection />
      <FeaturesSection />
      <LeagueSection />
      <AIAnalysisSection />
      <CommandsSection />
      <CTASection />
      <Footer />
    </div>
  );
}
