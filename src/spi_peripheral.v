`default_nettype none

module spi_peripheral (
    input  wire       clk,
    input  wire       rst_n,

    input  wire       sclk,
    input  wire       copi,
    input  wire       ncs,

    output reg  [7:0] en_reg_out_7_0,
    output reg  [7:0] en_reg_out_15_8,
    output reg  [7:0] en_reg_pwm_7_0,
    output reg  [7:0] en_reg_pwm_15_8,
    output reg  [7:0] pwm_duty_cycle
);

    // 2-flop synchronizers
    reg sclk_sync_0;
    reg sclk_sync_1;

    reg copi_sync_0;
    reg copi_sync_1;

    reg ncs_sync_0;
    reg ncs_sync_1;

    // Previous values for edge detection
    reg sclk_prev;
    reg ncs_prev;

    // SPI receiving state
    reg [15:0] shift_reg;
    reg [4:0]  bit_count;

    // Edge detection
    wire ncs_falling_edge;
    wire ncs_rising_edge;
    wire ncs_high;
    wire sclk_rising_edge;

    assign ncs_falling_edge = (ncs_prev == 1'b1) && (ncs_sync_1 == 1'b0);
    assign ncs_rising_edge  = (ncs_prev == 1'b0) && (ncs_sync_1 == 1'b1);
    assign ncs_high         = (ncs_sync_1 == 1'b1);
    assign sclk_rising_edge = (sclk_prev == 1'b0) && (sclk_sync_1 == 1'b1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sclk_sync_0 <= 1'b0;
            sclk_sync_1 <= 1'b0;

            copi_sync_0 <= 1'b0;
            copi_sync_1 <= 1'b0;

            ncs_sync_0 <= 1'b1;
            ncs_sync_1 <= 1'b1;

            sclk_prev <= 1'b0;
            ncs_prev  <= 1'b1;

            shift_reg <= 16'h0000;
            bit_count <= 5'd0;

            en_reg_out_7_0   <= 8'h00;
            en_reg_out_15_8  <= 8'h00;
            en_reg_pwm_7_0   <= 8'h00;
            en_reg_pwm_15_8  <= 8'h00;
            pwm_duty_cycle   <= 8'h00;
        end else begin
            // Synchronize external SPI signals into clk domain
            sclk_sync_0 <= sclk;
            sclk_sync_1 <= sclk_sync_0;

            copi_sync_0 <= copi;
            copi_sync_1 <= copi_sync_0;

            ncs_sync_0 <= ncs;
            ncs_sync_1 <= ncs_sync_0;

            // Save old synchronized values for edge detection
            sclk_prev <= sclk_sync_1;
            ncs_prev  <= ncs_sync_1;

            // When nCS falls, a new SPI transaction starts
            if (ncs_falling_edge) begin
                shift_reg <= 16'h0000;
                bit_count <= 5'd0;
            end

            // If nCS is high, transaction is inactive
            else if (ncs_high) begin
                bit_count <= 5'd0;
            end

            // If nCS is low and SCLK rises, sample one COPI bit
            else if (sclk_rising_edge) begin
                shift_reg <= {shift_reg[14:0], copi_sync_1};

                if (bit_count != 5'd16) begin
                    bit_count <= bit_count + 5'd1;
                end
            end

            // When nCS rises, the transaction is done. shift_reg already
            // holds the full 16-bit packet from the last SCLK edge, so
            // decode and commit the register write here.
            if (ncs_rising_edge && bit_count == 5'd16) begin
                if (shift_reg[15] == 1'b1) begin
                    case (shift_reg[14:8])
                        7'h00: en_reg_out_7_0  <= shift_reg[7:0];
                        7'h01: en_reg_out_15_8 <= shift_reg[7:0];
                        7'h02: en_reg_pwm_7_0  <= shift_reg[7:0];
                        7'h03: en_reg_pwm_15_8 <= shift_reg[7:0];
                        7'h04: pwm_duty_cycle  <= shift_reg[7:0];
                        default: begin
                            // Invalid address: ignore
                        end
                    endcase
                end
            end
        end
    end

endmodule