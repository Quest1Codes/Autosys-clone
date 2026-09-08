const kidsData = [
    {
        id: "kids-intro",
        title: "1. The Big Restaurant",
        icon: "ph-pizza",
        content: `
            <h3>Welcome to the AutoSys Restaurant!</h3>
            <p>Imagine this entire computer program is just a huge, busy restaurant. A restaurant has menus, chefs, managers, and a filing cabinet to keep track of receipts.</p>
            <p>Instead of cooking food, we are "running jobs" (like sending emails or doing math on a computer). But the steps are exactly the same!</p>
            <p>Sometimes, people don't just order one thing. They order a <strong>Combo Meal</strong> (we call these <em>Box Jobs</em>). A Combo Meal might have a Burger, Fries, and a Drink. The whole Combo Meal isn't "Finished" until <em>every single piece</em> is finished.</p>
            <div class="tut-alert">
                <strong>The Ultimate Goal:</strong> To make sure every order (job) is cooked (run) at the exact right time, in the exact right order, without anything catching on fire.
            </div>
        `
    },
    {
        id: "kids-ordering",
        title: "2. The Menu & Ordering (CLI & Parser)",
        icon: "ph-notebook",
        content: `
            <h3>Placing the Order</h3>
            <p>When you go to a restaurant, you speak to a waiter. But computers are a little dumb, so they need you to speak a very specific language called <strong>JIL</strong>.</p>
            <p>Here is how the order gets taken and translated:</p>
            <ul>
                <li><strong>The Waiter (CLI)</strong>: The waiter writes down exactly what you type.</li>
                <li><strong>The Word Chopper (Lexer)</strong>: This is a tiny robot that chops your sentence into individual words. If you say "I want a burger", it chops it into ["I", "want", "a", "burger"].</li>
                <li><strong>The Translator (Parser)</strong>: This is a smarter robot that looks at the chopped words and realizes, "Ah! This is an order for food!" It creates the official Order Ticket.</li>
            </ul>
            <br>
            <h4>The Exact Files Doing This:</h4>
            <div class="tut-grid-2">
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/cli/jil_cmd.py</h4>
                    <p><strong>The Waiter:</strong> The screen where you type in your new jobs.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/parser/lexer.py</h4>
                    <p><strong>The Word Chopper:</strong> It has a <code>Lexer</code> class that breaks your sentences into tiny tokens.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/parser/jil_parser.py</h4>
                    <p><strong>The Translator:</strong> It uses a <code>JilParser</code> class to understand the grammar and make an official Order Ticket.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/parser/condition_parser.py</h4>
                    <p><strong>The Special Requests Translator:</strong> Understands complex rules like "Only cook this if the fries are already done."</p>
                </div>
            </div>
        `
    },
    {
        id: "kids-cabinet",
        title: "3. The Filing Cabinet (Database & Models)",
        icon: "ph-archive-box",
        content: `
            <h3>Keeping Track of Everything</h3>
            <p>A restaurant needs to write down every order on a piece of paper, and save it in a safe filing cabinet so they don't forget it.</p>
            <ul>
                <li><strong>The Ticket (Models)</strong>: This is the shape of the paper. It has blanks for "Job Name", "Time", and "Is it finished?".</li>
                <li><strong>The Sticky Notes (Events)</strong>: Whenever something happens, someone slaps a sticky note on the ticket saying "Cooking!" or "Finished!".</li>
                <li><strong>The Cabinet (Database)</strong>: The actual metal box where we stuff all the tickets to keep them safe forever.</li>
            </ul>
            <br>
            <h4>The Exact Files Doing This:</h4>
            <div class="tut-grid-2">
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/models/job.py</h4>
                    <p><strong>The Ticket Blank:</strong> Defines what information a job needs (name, owner, what machine to run on).</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/models/event.py</h4>
                    <p><strong>The Sticky Notes:</strong> Records every time the job changes status (like going from STARTING to RUNNING).</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/db/schema.py</h4>
                    <p><strong>The Metal Folders:</strong> Sets up the actual tables inside the SQLite database.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/db/repository.py</h4>
                    <p><strong>The Cabinet Keeper:</strong> The person whose entire job is just putting tickets into the cabinet and pulling them out when asked.</p>
                </div>
            </div>
        `
    },
    {
        id: "kids-manager",
        title: "4. The Manager (Scheduler)",
        icon: "ph-user-focus",
        content: `
            <h3>The Boss Who Makes the Rules</h3>
            <p>The Manager sits on a tall chair and stares at the filing cabinet all day. He has a lot of rules to check before he lets the chefs cook anything!</p>
            <ul>
                <li><strong>Rule 1: Time.</strong> Is it 5:00 PM yet?</li>
                <li><strong>Rule 2: Dependencies.</strong> Is the soup finished boiling? We can't serve the steak until the soup is done!</li>
                <li><strong>Rule 3: Combo Meals.</strong> If a customer ordered a Combo Meal, and the chef burns the fries, the Manager yells: "The whole meal is ruined!" and marks the whole Box Job as a FAILURE.</li>
            </ul>
            <br>
            <h4>The Exact Files Doing This:</h4>
            <div class="tut-grid-2">
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/scheduler/time_trigger.py</h4>
                    <p><strong>The Manager's Watch:</strong> Checks if a job has a <code>start_times</code> rule and triggers it when the clock matches.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/scheduler/condition_evaluator.py</h4>
                    <p><strong>The Dependency Checker:</strong> The <code>ConditionEvaluator</code> class checks if Job A is SUCCESS before starting Job B.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/scheduler/state_machine.py</h4>
                    <p><strong>The Rulebook:</strong> The <code>StateMachine</code> class enforces that an order MUST go from "Waiting" to "Cooking" to "Done". It can't jump from "Waiting" straight to "Done".</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/scheduler/box_manager.py</h4>
                    <p><strong>The Combo Meal Handler:</strong> Rolls up the success or failure of child jobs to their parent Box job.</p>
                </div>
            </div>
        `
    },
    {
        id: "kids-kitchen",
        title: "5. The Kitchen & Chef (Agent)",
        icon: "ph-fire",
        content: `
            <h3>Cooking the Food!</h3>
            <p>When the Manager yells "Go!", the order is sent over a Walkie-Talkie to the Kitchen. The Chef (Agent) actually cooks the food.</p>
            <p>How does a computer "cook"? It runs a <strong>Command</strong>! Like <code>echo "Hello World"</code> or running a Python script.</p>
            <p>When the Chef is done, he rings a bell and gives the Manager a number. This number is called an <strong>Exit Code</strong>.</p>
            <ul>
                <li>Exit Code <strong>0</strong> = Perfect! (SUCCESS)</li>
                <li>Exit Code <strong>1 or higher</strong> = I burned it! (FAILURE)</li>
            </ul>
            <br>
            <h4>The Exact Files Doing This:</h4>
            <div class="tut-grid-2">
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/agent/server.py</h4>
                    <p><strong>The Kitchen Window:</strong> Listens for incoming orders from the Walkie-Talkie.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/agent/dispatch.py</h4>
                    <p><strong>The Head Chef:</strong> Reads the ticket and decides which stove (environment) to use.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/agent/runner.py</h4>
                    <p><strong>The Actual Chef:</strong> Uses a tool called <code>subprocess</code> to run the command on the computer, waits for it to finish, and grabs the Exit Code.</p>
                </div>
            </div>
        `
    },
    {
        id: "kids-screens",
        title: "6. The TVs & Alarms (API & WCC)",
        icon: "ph-monitor",
        content: `
            <h3>Watching the Action</h3>
            <p>Customers want to look at a TV screen to see if their order is ready. If the kitchen catches on fire, a loud alarm needs to go off.</p>
            <ul>
                <li><strong>The Walkie-Talkie (API Layer)</strong>: The central radio tower that lets the TVs, the Manager, and the Kitchen all talk to each other.</li>
                <li><strong>The TV Screen (Web UI)</strong>: A nice website showing green checkmarks for finished food.</li>
                <li><strong>The Fire Alarm (Notifications)</strong>: Sending an email to the owner if the Chef burns the steak.</li>
            </ul>
            <br>
            <h4>The Exact Files Doing This:</h4>
            <div class="tut-grid-2">
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/app_server/main.py</h4>
                    <p><strong>The Walkie-Talkie Base:</strong> A FastAPI server that handles all the HTTP requests between the different parts.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/app_server/routers/jobs.py</h4>
                    <p><strong>The Walkie-Talkie Channel:</strong> The specific API endpoints used just for talking about jobs.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/wcc/app.py</h4>
                    <p><strong>The TV Screen:</strong> The Workload Control Center (WCC). It builds the HTML pages that users see in their web browsers.</p>
                </div>
                <div class="tut-card">
                    <h4><i class="ph-file-code"></i> autosys/notifications/alarm_manager.py</h4>
                    <p><strong>The Fire Alarm:</strong> Listens for FAILURE events and can trigger emails or SNMP traps to wake up the system administrators.</p>
                </div>
            </div>
        `
    }
];
